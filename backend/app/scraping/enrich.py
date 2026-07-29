"""Photo enrichment: fetch portal detail pages for the full gallery + floorplans.

Search results don't always carry everything — OnTheMarket caps at 5 photos and no
portal puts floorplan URLs in its search payload — so live listings get one polite
detail-page fetch each (rate-limited via base.fetch). Runs as a capped batch after
every poll cycle, and on demand (in a background thread) when a property page is
opened before its listings have been enriched.
"""
import json
import logging
import re
import threading

import httpx
from sqlmodel import select

from ..db import session_scope
from ..models import Listing, Property, utcnow
from .base import PortalBlockedError, fetch
from .normalizer import sync_property_media

log = logging.getLogger("housespotter.scraping.enrich")

# detail fetches per poll cycle; each costs one rate-limited request (~5s), so the
# backlog drains at ~BATCH_LIMIT per half hour without hammering the portals
BATCH_LIMIT = 30

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def _decode_page_model(html: str) -> dict:
    """Rightmove serializes window.__PAGE_MODEL devalue-style: {"data": "<json array>"}
    where each node's dict values / list items are indices into that array."""
    i = html.find("window.__PAGE_MODEL")
    if i < 0:
        raise ValueError("rightmove: __PAGE_MODEL not found in detail page")
    start = html.index("{", i)
    outer, _ = json.JSONDecoder().raw_decode(html[start:])
    nodes = json.loads(outer["data"])
    memo: dict[int, object] = {}

    def resolve(idx):
        if not isinstance(idx, int) or isinstance(idx, bool):
            return idx
        if idx < 0:
            return None  # devalue sentinel (undefined etc.)
        if idx in memo:
            return memo[idx]
        node = nodes[idx]
        if isinstance(node, dict):
            out: dict = {}
            memo[idx] = out
            for key, value in node.items():
                out[key] = resolve(value)
            return out
        if isinstance(node, list):
            out_list: list = []
            memo[idx] = out_list
            out_list.extend(resolve(value) for value in node)
            return out_list
        return node

    root = resolve(0)
    return root if isinstance(root, dict) else {}


def _rightmove_media(listing: Listing) -> tuple[list[str], list[str]]:
    resp = fetch("rightmove", listing.url)
    pd = _decode_page_model(resp.text).get("propertyData") or {}
    images = [
        img["url"] for img in pd.get("images") or []
        if isinstance(img, dict) and img.get("url")
    ]
    floorplans = [
        fp["url"] for fp in pd.get("floorplans") or []
        if isinstance(fp, dict) and fp.get("url") and fp.get("type") in (None, "IMAGE")
    ]
    return images, floorplans


def _onthemarket_media(listing: Listing) -> tuple[list[str], list[str]]:
    resp = fetch("onthemarket", listing.url)
    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        raise ValueError("onthemarket: __NEXT_DATA__ not found in detail page")
    prop = json.loads(m.group(1))["props"]["initialReduxState"].get("property") or {}
    images = [
        img.get("largeUrl") or img.get("url")
        for img in prop.get("images") or []
        if isinstance(img, dict) and (img.get("largeUrl") or img.get("url"))
    ]
    floorplans = [
        fp.get("original") or fp.get("largeUrl") or fp.get("url")
        for fp in prop.get("floorplans") or []
        if isinstance(fp, dict) and (fp.get("original") or fp.get("largeUrl") or fp.get("url"))
    ]
    return images, floorplans


# Zoopla is playwright-gated and Purplebricks galleries are derived in its adapter,
# so only these two portals need detail-page fetches.
ENRICHERS = {
    "rightmove": _rightmove_media,
    "onthemarket": _onthemarket_media,
}


def _enrich_listing(session, listing: Listing) -> None:
    """One detail-page fetch → store gallery + floorplans, resync the property.
    Raises PortalBlockedError; all other failures mark the listing enriched anyway
    (keeping its search photos) so it isn't retried forever."""
    try:
        images, floorplans = ENRICHERS[listing.portal](listing)
        if images:
            listing.gallery_urls = images
        if floorplans:
            listing.floorplan_urls = floorplans
    except PortalBlockedError:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("enrich failed for %s %s: %s", listing.portal, listing.portal_id, exc)
    listing.photos_enriched_at = utcnow()
    session.add(listing)
    prop = session.get(Property, listing.property_id)
    if prop:
        sync_property_media(session, prop, listing)
        session.add(prop)


def _pending_query(extra_filter=None):
    query = select(Listing).where(
        Listing.portal.in_(ENRICHERS),  # type: ignore[attr-defined]
        Listing.status != "removed",
        Listing.photos_enriched_at == None,  # noqa: E711
    )
    if extra_filter is not None:
        query = query.where(extra_filter)
    return query.order_by(Listing.first_seen.desc())  # type: ignore[attr-defined]


def enrich_pending(limit: int = BATCH_LIMIT) -> int:
    """Enrich up to `limit` newest un-enriched live listings. Returns count done."""
    from .jobs import _pause_portal, _portal_paused

    with session_scope() as session:
        ids = [l.id for l in session.exec(_pending_query().limit(limit)).all()]
    done = 0
    skip_portals: set[str] = set()
    for listing_id in ids:
        with session_scope() as session:
            listing = session.get(Listing, listing_id)
            if not listing or listing.photos_enriched_at is not None:
                continue
            if listing.portal in skip_portals or _portal_paused(listing.portal):
                continue
            try:
                _enrich_listing(session, listing)
            except PortalBlockedError as exc:
                log.warning("enrichment blocked: %s", exc)
                _pause_portal(listing.portal)
                skip_portals.add(listing.portal)
                continue
            session.commit()
            done += 1
    if done:
        log.info("photo enrichment: %d listings enriched", done)
    return done


# ---- On-demand enrichment when a property page is opened ----

_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def enrich_property(property_id: int) -> None:
    with session_scope() as session:
        listings = session.exec(
            _pending_query(Listing.property_id == property_id)
        ).all()
        for listing in listings:
            try:
                _enrich_listing(session, listing)
            except PortalBlockedError as exc:
                log.warning("on-demand enrichment blocked: %s", exc)
                break
        session.commit()


def enrich_property_async(property_id: int) -> None:
    """Fire-and-forget enrichment; deduped so repeated page views don't stack threads."""
    with _inflight_lock:
        if property_id in _inflight:
            return
        _inflight.add(property_id)

    def _run():
        try:
            enrich_property(property_id)
        except Exception:
            log.exception("background enrichment failed for property %s", property_id)
        finally:
            with _inflight_lock:
                _inflight.discard(property_id)

    threading.Thread(target=_run, daemon=True, name=f"enrich-{property_id}").start()
