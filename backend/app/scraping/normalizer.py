"""Upsert normalized listings into the canonical Property/Listing store with de-duplication."""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..models import Listing, Property, utcnow
from .base import NormalizedListing

log = logging.getLogger("housespotter.normalizer")

# ~30m in degrees latitude; used for cross-portal geo matching
_GEO_EPS = 0.0003

# Same-mode listings priced within this fraction count as the same home. New-build
# developments advertise many units under one marketing address and one map pin, so
# a bigger gap means a different plot, not a portal-to-portal price discrepancy.
_PRICE_TOLERANCE = 0.05


def _norm_address(address: str) -> str:
    return re.sub(r"[^a-z0-9]", "", address.lower())


# Outcode ("CB4") optionally followed by the incode ("3NG"), at the end of the text —
# portal display addresses usually finish with one ("Mere Way, Cambridge, CB4")
_OUTCODE_RE = re.compile(r"\b([A-Za-z]{1,2}\d{1,2}[A-Za-z]?)(?:\s+\d[A-Za-z]{2})?\s*$")


def extract_outcode(text: str | None) -> str | None:
    if not text:
        return None
    m = _OUTCODE_RE.search(text.strip())
    return m.group(1).upper() if m else None


def _payload_hash(nl: NormalizedListing) -> str:
    payload = {
        "price": nl.price,
        "status": nl.status,
        "address": nl.address,
        "beds": nl.beds,
        "baths": nl.baths,
        "type": nl.property_type,
        "tenure": nl.tenure,
        "description": nl.description,
        "features": nl.features,
        "images": nl.image_urls,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _merge_compatible(session: Session, prop: Property, nl: NormalizedListing) -> bool:
    """Whether nl can share a Property with prop's existing listings.

    Rejects cross-mode merges (a rental never joins a sale property) and same-mode
    merges whose prices are too far apart — both happen inside developments, where
    every unit shares one marketing address and map pin."""
    listings = list(session.exec(select(Listing).where(Listing.property_id == prop.id)).all())
    if not listings:
        return True
    same_mode = [l for l in listings if l.mode == nl.mode]
    if not same_mode:
        return False
    # A portal lists a given home once — a second live id on the same portal is
    # another unit. (Same portal_id never reaches here: upsert matched it already.)
    if nl.status != "removed" and any(
        l.portal == nl.portal and l.status != "removed" for l in same_mode
    ):
        return False
    priced = [l for l in same_mode if l.price is not None]
    if nl.price is None or not priced:
        return True
    live = [l for l in priced if l.status != "removed"] or priced
    return any(
        abs(l.price - nl.price) <= _PRICE_TOLERANCE * max(l.price, nl.price) for l in live
    )


def _find_property(session: Session, nl: NormalizedListing) -> Property | None:
    key = _dedupe_key(nl)
    # Several properties can share a base address key (sibling units in a development
    # carry a ":portal:portal_id" suffix) — merge into the first compatible one.
    candidates = session.exec(
        select(Property).where(
            (Property.dedupe_key == key) | (Property.dedupe_key.startswith(key + ":"))  # type: ignore[arg-type]
        )
    ).all()
    for cand in candidates:
        if _merge_compatible(session, cand, nl):
            return cand
    # Cross-portal fuzzy match: same coords (±30m), beds and type
    if nl.lat is not None and nl.lng is not None and nl.beds is not None:
        candidates = session.exec(
            select(Property).where(
                Property.lat.between(nl.lat - _GEO_EPS, nl.lat + _GEO_EPS),  # type: ignore[union-attr]
                Property.lng.between(nl.lng - _GEO_EPS, nl.lng + _GEO_EPS),  # type: ignore[union-attr]
                Property.beds == nl.beds,
            )
        ).all()
        for cand in candidates:
            if cand.property_type == nl.property_type and _merge_compatible(session, cand, nl):
                return cand
    return None


def _dedupe_key(nl: NormalizedListing) -> str:
    addr = _norm_address(nl.address)
    if addr:
        return f"a:{addr}:{nl.beds or ''}"
    return f"g:{round(nl.lat or 0, 5)}:{round(nl.lng or 0, 5)}:{nl.beds or ''}"


def _sibling_conflict(session: Session, prop: Property, listing: Listing) -> bool:
    """A live listing conflicts with its property's other listings when a sibling is
    live on the same portal/mode (a portal lists a home once) or every priced live
    same-mode sibling is beyond the price tolerance. Happens when a listing that was
    'removed' at v13-split time gets re-listed, or drifts in price after a merge."""
    if listing.status == "removed":
        return False
    siblings = [
        l for l in session.exec(select(Listing).where(Listing.property_id == prop.id)).all()
        if l.id != listing.id and l.mode == listing.mode
    ]
    if any(l.portal == listing.portal and l.status != "removed" for l in siblings):
        return True
    if listing.price is None:
        return False
    priced = [l for l in siblings if l.status != "removed" and l.price is not None]
    return bool(priced) and all(
        abs(l.price - listing.price) > _PRICE_TOLERANCE * max(l.price, listing.price)
        for l in priced
    )


def _detach_listing(session: Session, listing: Listing, prop: Property) -> Property:
    """Move a conflicting listing onto its own sibling property (the live-data
    counterpart of the v13 development-split migration)."""
    key = f"{prop.dedupe_key}:{listing.portal}:{listing.portal_id}"
    target = session.exec(select(Property).where(Property.dedupe_key == key)).first()
    if target is None:
        target = Property(
            dedupe_key=key,
            address=prop.address,
            postcode=prop.postcode,
            outcode=prop.outcode,
            lat=prop.lat,
            lng=prop.lng,
            beds=prop.beds,
            baths=prop.baths,
            property_type=prop.property_type,
            tenure=prop.tenure,
            furnish_type=prop.furnish_type,
            floor_area_sqm=prop.floor_area_sqm,
            epc=prop.epc,
            features=prop.features,
            description=prop.description,
        )
        session.add(target)
        session.flush()
    listing.property_id = target.id
    session.add(listing)
    session.flush()
    sync_property_media(session, target, listing)
    # the old property's chimera photos/scores may have come from the moved listing
    sync_property_media(session, prop)
    from ..models import MatchScore

    for score in session.exec(select(MatchScore).where(MatchScore.property_id == prop.id)).all():
        session.delete(score)
    session.add(prop)
    log.info(
        "Detached %s:%s from property %s into %s (conflicting siblings)",
        listing.portal, listing.portal_id, prop.id, target.id,
    )
    return target


def _richer(new_value, old_value):
    """Prefer non-empty new data but never blank out existing data."""
    if new_value in (None, "", []):
        return old_value
    return new_value


def upsert_listing(session: Session, nl: NormalizedListing) -> str:
    """Returns 'new' | 'updated' | 'unchanged'."""
    listing = session.exec(
        select(Listing).where(Listing.portal == nl.portal, Listing.portal_id == nl.portal_id)
    ).first()
    now = utcnow()
    new_hash = _payload_hash(nl)

    if listing:
        listing.last_seen = now
        if listing.status == "removed":
            listing.status = nl.status  # re-listed
        unchanged = listing.payload_hash == new_hash
        if not unchanged:
            if nl.price is not None and nl.price != listing.price:
                listing.price_history = listing.price_history + [
                    {"date": now.date().isoformat(), "price": nl.price}
                ]
                listing.price = nl.price
            listing.price_qualifier = nl.price_qualifier or listing.price_qualifier
            listing.status = nl.status
            listing.payload_hash = new_hash
            if listing.photos_enriched_at is not None and listing.image_urls != nl.image_urls:
                # the portal's photo set changed → the enriched gallery is stale
                listing.photos_enriched_at = None
                listing.gallery_urls = []
            listing.image_urls = nl.image_urls
            if nl.floorplan_urls:
                listing.floorplan_urls = nl.floorplan_urls
        prop = session.get(Property, listing.property_id)
        if prop and _sibling_conflict(session, prop, listing):
            # re-listed or re-priced into conflict with its merged siblings
            prop = _detach_listing(session, listing, prop)
        if prop and not unchanged:
            _update_property(prop, nl, now)
            sync_property_media(session, prop, listing)
            session.add(prop)
        session.add(listing)
        return "unchanged" if unchanged else "updated"

    prop = _find_property(session, nl)
    if prop is None:
        key = _dedupe_key(nl)
        if session.exec(select(Property).where(Property.dedupe_key == key)).first() is not None:
            # a same-address property exists but wasn't merge-compatible: another
            # unit in the same development — suffix the key to keep it unique
            key = f"{key}:{nl.portal}:{nl.portal_id}"
        prop = Property(dedupe_key=key)
        _update_property(prop, nl, now)
        session.add(prop)
        session.flush()  # assign id
    else:
        _update_property(prop, nl, now)
        session.add(prop)

    listing = Listing(
        property_id=prop.id,
        portal=nl.portal,
        portal_id=nl.portal_id,
        url=nl.url,
        mode=nl.mode,
        price=nl.price,
        price_qualifier=nl.price_qualifier,
        status=nl.status,
        first_seen=now,
        last_seen=now,
        first_listed=nl.first_listed,
        price_history=(
            [{"date": now.date().isoformat(), "price": nl.price}] if nl.price is not None else []
        ),
        payload_hash=new_hash,
        image_urls=nl.image_urls,
        floorplan_urls=nl.floorplan_urls,
        raw=nl.raw,
    )
    session.add(listing)
    sync_property_media(session, prop, listing)
    session.add(prop)
    return "new"


def _update_property(prop: Property, nl: NormalizedListing, now: datetime) -> None:
    prop.address = _richer(nl.address, prop.address)
    prop.postcode = _richer(nl.postcode, prop.postcode)
    if prop.postcode and not prop.outcode:
        prop.outcode = prop.postcode.split()[0].upper()
    if not prop.outcode:
        prop.outcode = extract_outcode(prop.address)
    prop.lat = _richer(nl.lat, prop.lat)
    prop.lng = _richer(nl.lng, prop.lng)
    prop.beds = _richer(nl.beds, prop.beds)
    prop.baths = _richer(nl.baths, prop.baths)
    prop.property_type = _richer(nl.property_type, prop.property_type)
    prop.tenure = _richer(nl.tenure, prop.tenure)
    prop.floor_area_sqm = _richer(nl.floor_area_sqm, prop.floor_area_sqm)
    prop.epc = _richer(nl.epc, prop.epc)
    prop.features = _richer(nl.features, prop.features)
    if len(nl.description) > len(prop.description):
        prop.description = nl.description
    prop.updated_at = now


def sync_property_media(session: Session, prop: Property, current: Listing | None = None) -> None:
    """Property photos/floorplans = the richest single listing's set, so one portal's
    thin payload (e.g. Zoopla's single cover image) can never clobber a full gallery.
    Enriched detail-page galleries take precedence over search-result sets."""
    listings = list(session.exec(select(Listing).where(Listing.property_id == prop.id)).all())
    if current is not None and current not in listings:
        listings.append(current)
    live = [l for l in listings if l.status != "removed"] or listings
    images = max((l.gallery_urls or l.image_urls or [] for l in live), key=len, default=[])
    floorplans = max((l.floorplan_urls or [] for l in live), key=len, default=[])
    if images:
        prop.image_urls = images
    if floorplans:
        prop.floorplan_urls = floorplans


def mark_missing_as_removed(
    session: Session, portal: str, mode: str, seen_portal_ids: set[str], grace_days: int = 3
) -> int:
    """Mark listings not seen for `grace_days` as removed (called after a full successful poll)."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    stale = session.exec(
        select(Listing).where(
            Listing.portal == portal,
            Listing.mode == mode,
            Listing.status != "removed",
        )
    ).all()
    count = 0
    for listing in stale:
        last_seen = listing.last_seen
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if listing.portal_id not in seen_portal_ids and last_seen < cutoff:
            listing.status = "removed"
            session.add(listing)
            count += 1
    return count
