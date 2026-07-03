"""Zoopla adapter — EXPERIMENTAL, disabled by default.

Zoopla sits behind Cloudflare and 403s plain HTTP clients, so this adapter requires the
Playwright fallback (HS_PLAYWRIGHT_FALLBACK=true + `pip install playwright` +
`playwright install chromium`, or system chromium on the Pi via
PLAYWRIGHT_BROWSERS_PATH/executable_path). The __NEXT_DATA__ parse below is best-effort
and may need adjusting against a real fetched page the first time it is enabled.
"""
import json
import logging
import re

from .base import NormalizedListing, PortalBlockedError, polite_wait, USER_AGENT

log = logging.getLogger("housespotter.scraping.zoopla")

PORTAL = "zoopla"
BASE = "https://www.zoopla.co.uk"
MAX_PAGES = 3

_TYPE_MAP = {
    "flat": "flat",
    "flats": "flat",
    "apartment": "flat",
    "maisonette": "flat",
    "studio": "flat",
    "detached": "detached",
    "detached house": "detached",
    "semi_detached": "semi-detached",
    "semi-detached house": "semi-detached",
    "terraced": "terraced",
    "terraced house": "terraced",
    "end terrace house": "terraced",
    "town house": "terraced",
    "bungalow": "bungalow",
    "detached bungalow": "bungalow",
    "cottage": "detached",
    "land": "land",
    "park home": "park-home",
}


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")


def _fetch_rendered(url: str) -> str:
    """Fetch via Playwright Chromium (survives the Cloudflare challenge in most cases)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PortalBlockedError("zoopla: playwright not installed") from exc

    polite_wait(PORTAL)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="en-GB")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)  # let any challenge resolve
            html = page.content()
        finally:
            browser.close()
    if "__NEXT_DATA__" not in html:
        raise PortalBlockedError("zoopla: challenge not passed (no __NEXT_DATA__)")
    return html


def _walk_for_listings(obj) -> list[dict]:
    """Find the regular-listings array anywhere in __NEXT_DATA__ (shape shifts between deploys)."""
    hits: list[list[dict]] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    key in ("regularListingsFormatted", "listings", "regular")
                    and isinstance(value, list)
                    and value
                    and isinstance(value[0], dict)
                    and ("listingId" in value[0] or "listing_id" in value[0] or "price" in value[0])
                ):
                    hits.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return max(hits, key=len) if hits else []


def _to_listing(item: dict, mode: str) -> NormalizedListing | None:
    listing_id = item.get("listingId") or item.get("listing_id") or item.get("id")
    if not listing_id:
        return None
    price_raw = item.get("price")
    price = None
    if isinstance(price_raw, (int, float)):
        price = int(price_raw)
    elif isinstance(price_raw, str):
        digits = re.sub(r"[^\d]", "", price_raw)
        price = int(digits) if digits else None
        if mode == "rent" and price and "pw" in price_raw.lower():
            price = round(price * 52 / 12)

    pos = item.get("pos") or item.get("location") or {}
    prop_type = (item.get("propertyType") or item.get("property_type") or "").lower() or None
    if prop_type:
        prop_type = _TYPE_MAP.get(prop_type, prop_type)
    image = item.get("image") or {}
    image_url = image.get("src") if isinstance(image, dict) else image

    return NormalizedListing(
        portal=PORTAL,
        portal_id=str(listing_id),
        url=f"{BASE}{item.get('listingUris', {}).get('detail', f'/details/{listing_id}/')}",
        mode=mode,
        price=price,
        price_qualifier="pcm" if mode == "rent" else item.get("priceTitle") or None,
        address=item.get("address") or item.get("displayAddress") or "",
        lat=pos.get("lat") or pos.get("latitude"),
        lng=pos.get("lng") or pos.get("longitude"),
        beds=item.get("beds") or item.get("numBeds"),
        baths=item.get("baths") or item.get("numBaths"),
        property_type=prop_type,
        description=item.get("summaryDescription") or item.get("title") or "",
        image_urls=[image_url] if image_url else [],
        raw=None,
    )


class ZooplaAdapter:
    portal = PORTAL

    def search(self, profile, location: dict) -> list[NormalizedListing]:
        channel = "for-sale" if profile.mode == "buy" else "to-rent"
        slug = _slug(location["label"])
        query = []
        if profile.max_price:
            query.append(f"price_max={profile.max_price}")
        if profile.min_price:
            query.append(f"price_min={profile.min_price}")
        if profile.min_beds:
            query.append(f"beds_min={profile.min_beds}")
        if profile.max_beds:
            query.append(f"beds_max={profile.max_beds}")
        radius_km = location.get("radius_km")
        if radius_km:
            query.append(f"radius={round(radius_km * 0.621371)}")

        results: list[NormalizedListing] = []
        for page in range(1, MAX_PAGES + 1):
            qs = "&".join(query + ([f"pn={page}"] if page > 1 else []))
            url = f"{BASE}/{channel}/property/{slug}/" + (f"?{qs}" if qs else "")
            html = _fetch_rendered(url)
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
            )
            if not m:
                break
            items = _walk_for_listings(json.loads(m.group(1)))
            if not items:
                break
            for item in items:
                try:
                    nl = _to_listing(item, profile.mode)
                    if nl:
                        results.append(nl)
                except Exception:
                    log.exception("zoopla: failed to normalize an item")
            if len(items) < 25:
                break
        log.info("zoopla: %s @ %s → %d listings", profile.mode, location["label"], len(results))
        return results
