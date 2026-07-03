"""Rightmove adapter — parses the __NEXT_DATA__ JSON embedded in search result pages."""
import json
import logging
import re
from datetime import datetime

from sqlmodel import Session

from ..db import engine
from ..models import Meta
from .base import NormalizedListing, fetch

log = logging.getLogger("housespotter.scraping.rightmove")

PORTAL = "rightmove"
BASE = "https://www.rightmove.co.uk"
TYPEAHEAD = "https://los.rightmove.co.uk/typeahead"
MAX_PAGES = 4  # 25 results/page → up to 100 per location per poll

# km → nearest Rightmove-accepted radius (miles)
_RADII_MILES = [0.25, 0.5, 1.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0]

_TYPE_MAP = {
    "apartment": "flat",
    "flat": "flat",
    "maisonette": "flat",
    "penthouse": "flat",
    "studio": "flat",
    "detached": "detached",
    "detached house": "detached",
    "detached bungalow": "bungalow",
    "semi-detached": "semi-detached",
    "semi-detached house": "semi-detached",
    "semi-detached bungalow": "bungalow",
    "terraced": "terraced",
    "terraced house": "terraced",
    "end of terrace": "terraced",
    "end of terrace house": "terraced",
    "mid terrace": "terraced",
    "town house": "terraced",
    "townhouse": "terraced",
    "bungalow": "bungalow",
    "cottage": "detached",
    "house": "house",
    "land": "land",
    "park home": "park-home",
}

# our taxonomy → Rightmove propertyTypes search param
_SEARCH_TYPES = {
    "detached": "detached",
    "semi-detached": "semi-detached",
    "terraced": "terraced",
    "flat": "flat",
    "bungalow": "bungalow",
    "land": "land",
    "park-home": "park-home",
}


def _typeahead(query: str) -> str | None:
    resp = fetch(
        PORTAL, TYPEAHEAD, params={"query": query, "limit": 10},
        headers={"Accept": "application/json"},
    )
    for match in resp.json().get("matches", []):
        if match.get("type") in ("REGION", "OUTCODE", "POSTCODE"):
            return f"{match['type']}^{match['id']}"
    return None


def resolve_location(label: str, lat: float | None = None, lng: float | None = None) -> str | None:
    """Place name → Rightmove locationIdentifier (e.g. 'REGION^580'), cached in Meta.
    Labels Rightmove doesn't know as a region (e.g. 'South Cambridge') fall back to the
    location's postcode district via its coordinates."""
    cache_key = f"rmloc:{label.strip().lower()}"
    with Session(engine) as session:
        row = session.get(Meta, cache_key)
        if row:
            return row.value or None

    identifier = _typeahead(label)
    if not identifier and lat is not None and lng is not None:
        from ..research.geo import reverse_outcode

        outcode = reverse_outcode(lat, lng)
        if outcode:
            log.info("Rightmove: %r not a region; falling back to outcode %s", label, outcode)
            identifier = _typeahead(outcode)

    with Session(engine) as session:
        session.merge(Meta(key=cache_key, value=identifier or ""))
        session.commit()
    if not identifier:
        log.warning("Rightmove could not resolve location %r", label)
    return identifier


def _radius_miles(radius_km: float | None) -> float:
    if not radius_km:
        return 0.0
    miles = radius_km * 0.621371
    return min(_RADII_MILES, key=lambda r: abs(r - miles))


def _norm_type(sub_type: str | None) -> str | None:
    if not sub_type:
        return None
    return _TYPE_MAP.get(sub_type.strip().lower(), sub_type.strip().lower())


def _parse_size(display_size: str | None) -> float | None:
    if not display_size:
        return None
    m = re.search(r"([\d,]+)\s*sq\.?\s*ft", display_size)
    if m:
        return round(int(m.group(1).replace(",", "")) * 0.092903, 1)
    m = re.search(r"([\d,]+)\s*(?:sq\.?\s*m|m²)", display_size)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not m:
        raise ValueError("rightmove: __NEXT_DATA__ not found in page")
    return json.loads(m.group(1))


def _to_listing(prop: dict, mode: str) -> NormalizedListing:
    price_info = prop.get("price") or {}
    display = (price_info.get("displayPrices") or [{}])[0]
    qualifier = display.get("displayPriceQualifier") or None
    amount = price_info.get("amount")
    if mode == "rent":
        freq = (price_info.get("frequency") or "").lower()
        if freq == "weekly" and amount:
            amount = round(amount * 52 / 12)
        qualifier = "pcm"

    features = []
    for kf in prop.get("keyFeatures") or []:
        text = kf.get("description") if isinstance(kf, dict) else str(kf)
        if text:
            features.append(text)

    location = prop.get("location") or {}
    tenure = ((prop.get("tenure") or {}).get("tenureType") or "").lower() or None

    return NormalizedListing(
        portal=PORTAL,
        portal_id=str(prop["id"]),
        url=f"{BASE}/properties/{prop['id']}",
        mode=mode,
        price=amount,
        price_qualifier=qualifier,
        status="live",
        address=prop.get("displayAddress") or "",
        lat=location.get("latitude"),
        lng=location.get("longitude"),
        beds=prop.get("bedrooms"),
        baths=prop.get("bathrooms"),
        property_type=_norm_type(prop.get("propertySubType")),
        tenure=tenure,
        floor_area_sqm=_parse_size(prop.get("displaySize")),
        features=features,
        description=prop.get("summary") or "",
        image_urls=[img["srcUrl"] for img in (prop.get("images") or []) if img.get("srcUrl")],
        first_listed=_parse_dt(prop.get("firstVisibleDate")),
        raw=None,  # search-result payload is large; keep DB lean
    )


class RightmoveAdapter:
    portal = PORTAL

    def search(self, profile, location: dict) -> list[NormalizedListing]:
        identifier = resolve_location(location["label"], location.get("lat"), location.get("lng"))
        if not identifier:
            raise ValueError(f"rightmove: could not resolve location '{location['label']}'")

        channel = "property-for-sale" if profile.mode == "buy" else "property-to-rent"
        params: dict = {
            "locationIdentifier": identifier,
            "radius": _radius_miles(location.get("radius_km")),
            "sortType": 6,  # newest first
            "index": 0,
        }
        if profile.min_price:
            params["minPrice"] = profile.min_price
        if profile.max_price:
            params["maxPrice"] = profile.max_price
        if profile.min_beds:
            params["minBedrooms"] = profile.min_beds
        if profile.max_beds:
            params["maxBedrooms"] = profile.max_beds
        wanted_types = [_SEARCH_TYPES[t] for t in (profile.property_types or []) if t in _SEARCH_TYPES]
        if wanted_types:
            params["propertyTypes"] = ",".join(wanted_types)

        results: list[NormalizedListing] = []
        for page in range(MAX_PAGES):
            params["index"] = page * 25
            resp = fetch(PORTAL, f"{BASE}/{channel}/find.html", params=params)
            data = _extract_next_data(resp.text)
            sr = data["props"]["pageProps"]["searchResults"]
            props = sr.get("properties") or []
            for prop in props:
                try:
                    results.append(_to_listing(prop, profile.mode))
                except Exception:
                    log.exception("rightmove: failed to normalize property %s", prop.get("id"))
            total = int(str(sr.get("resultCount") or 0).replace(",", ""))
            if (page + 1) * 25 >= total or not props:
                break
        log.info("rightmove: %s @ %s → %d listings", profile.mode, location["label"], len(results))
        return results
