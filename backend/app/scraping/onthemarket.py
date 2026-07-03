"""OnTheMarket adapter — parses __NEXT_DATA__ → props.initialReduxState.results.list."""
import json
import logging
import re

from .base import NormalizedListing, fetch

log = logging.getLogger("housespotter.scraping.onthemarket")

PORTAL = "onthemarket"
BASE = "https://www.onthemarket.com"
MAX_PAGES = 3  # 30 results/page

_RADII_MILES = [0.25, 0.5, 1, 3, 5, 10, 15, 20, 30]

_TYPE_MAP = {
    "flat": "flat",
    "apartment": "flat",
    "flats": "flat",
    "maisonette": "flat",
    "studio": "flat",
    "detached house": "detached",
    "detached": "detached",
    "semi-detached house": "semi-detached",
    "semi-detached": "semi-detached",
    "terraced house": "terraced",
    "terraced": "terraced",
    "end of terrace house": "terraced",
    "town house": "terraced",
    "bungalow": "bungalow",
    "detached bungalow": "bungalow",
    "semi-detached bungalow": "bungalow",
    "house": "house",
    "cottage": "detached",
    "land": "land",
    "plot": "land",
    "park home": "park-home",
}


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")


def _parse_price(text: str | None, mode: str) -> tuple[int | None, str | None]:
    if not text:
        return None, None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None, text.strip() or None  # "POA"
    amount = int(digits)
    if mode == "rent":
        if "pw" in text.lower():
            amount = round(amount * 52 / 12)
        return amount, "pcm"
    return amount, None


def _norm_type(humanised: str | None) -> str | None:
    if not humanised:
        return None
    return _TYPE_MAP.get(humanised.strip().lower(), humanised.strip().lower())


def _to_listing(prop: dict, mode: str) -> NormalizedListing:
    price, qualifier = _parse_price(prop.get("price"), mode)
    if prop.get("price-qualifier"):
        qualifier = prop["price-qualifier"]
    location = prop.get("location") or {}
    images = []
    for img in prop.get("images") or []:
        url = img.get("default") if isinstance(img, dict) else img
        if url:
            images.append(url)
    if not images:
        cover = prop.get("cover-image") or {}
        if isinstance(cover, dict) and cover.get("default"):
            images.append(cover["default"])

    portal_id = str(prop["id"])
    details_url = prop.get("details-url") or f"/details/{portal_id}/"
    return NormalizedListing(
        portal=PORTAL,
        portal_id=portal_id,
        url=f"{BASE}{details_url}",
        mode=mode,
        price=price,
        price_qualifier=qualifier,
        status="live",
        address=prop.get("address") or "",
        lat=location.get("lat"),
        lng=location.get("lon"),
        beds=prop.get("bedrooms"),
        baths=prop.get("bathrooms"),
        property_type=_norm_type(prop.get("humanised-property-type")),
        description=prop.get("property-title") or "",
        image_urls=images,
        raw=None,
    )


def _extract_results(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not m:
        raise ValueError("onthemarket: __NEXT_DATA__ not found")
    return json.loads(m.group(1))["props"]["initialReduxState"]["results"]


class OnTheMarketAdapter:
    portal = PORTAL

    def _resolve_slug(self, location: dict, channel: str) -> str | None:
        """OTM only knows real place slugs; unknown labels fall back to the outcode slug."""
        import httpx
        from sqlmodel import Session

        from ..db import engine
        from ..models import Meta

        cache_key = f"otmslug:{_slug(location['label'])}"
        with Session(engine) as session:
            row = session.get(Meta, cache_key)
            if row:
                return row.value or None

        def _store(value: str) -> None:
            with Session(engine) as session:
                session.merge(Meta(key=cache_key, value=value))
                session.commit()

        candidates = [_slug(location["label"])]
        if location.get("lat") is not None:
            from ..research.geo import reverse_outcode

            outcode = reverse_outcode(location["lat"], location.get("lng"))
            if outcode:
                candidates.append(outcode.lower())
        for slug in candidates:
            try:
                fetch(PORTAL, f"{BASE}/{channel}/property/{slug}/", params={"page": 1})
                _store(slug)
                return slug
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    log.info("onthemarket: no page for slug %r, trying fallback", slug)
                    continue
                raise
        _store("")
        return None

    def search(self, profile, location: dict) -> list[NormalizedListing]:
        channel = "for-sale" if profile.mode == "buy" else "to-rent"
        slug = self._resolve_slug(location, channel)
        if not slug:
            raise ValueError(f"onthemarket: no results page for '{location['label']}'")
        params: dict = {}
        if profile.min_price:
            params["min-price"] = profile.min_price
        if profile.max_price:
            params["max-price"] = profile.max_price
        if profile.min_beds:
            params["min-bedrooms"] = profile.min_beds
        if profile.max_beds:
            params["max-bedrooms"] = profile.max_beds
        radius_km = location.get("radius_km")
        if radius_km:
            miles = radius_km * 0.621371
            params["radius"] = min(_RADII_MILES, key=lambda r: abs(r - miles))

        results: list[NormalizedListing] = []
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                params["page"] = page
            resp = fetch(PORTAL, f"{BASE}/{channel}/property/{slug}/", params=params)
            data = _extract_results(resp.text)
            props = data.get("list") or []
            for prop in props:
                try:
                    results.append(_to_listing(prop, profile.mode))
                except Exception:
                    log.exception("onthemarket: failed to normalize %s", prop.get("id"))
            total = int(data.get("totalResults") or 0)
            if page * 30 >= total or not props:
                break
        log.info("onthemarket: %s @ %s → %d listings", profile.mode, location["label"], len(results))
        return results
