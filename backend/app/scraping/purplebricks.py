"""Purplebricks adapter — parses __NEXT_DATA__ → props.pageProps.ssrResultData.

Purplebricks searches by a single town slug (e.g. /property-for-sale/cambridge);
the label is slugified, falling back to the coordinates' admin district. Listings
carry a postcode but no coordinates, so we geocode the postcode (postcodes.io) to
enable map pins, milestone/station scoring and cross-portal dedupe. Sales only —
Purplebricks carries almost no rentals, so rent searches return empty."""
import json
import logging
import re

from .base import NormalizedListing, fetch

log = logging.getLogger("housespotter.scraping.purplebricks")

PORTAL = "purplebricks"
BASE = "https://www.purplebricks.co.uk"
MAX_PAGES = 3  # 10 results/page

# Purplebricks accepts an arbitrary miles radius; keep it sane
_MAX_RADIUS = 40

_TYPE_MAP = {
    "flat": "flat",
    "apartment": "flat",
    "maisonette": "flat",
    "studio": "flat",
    "penthouse": "flat",
    "detached": "detached",
    "detached house": "detached",
    "semi-detached": "semi-detached",
    "semi-detached house": "semi-detached",
    "terraced": "terraced",
    "terraced house": "terraced",
    "end terrace": "terraced",
    "end of terrace": "terraced",
    "mid terrace": "terraced",
    "town house": "terraced",
    "townhouse": "terraced",
    "bungalow": "bungalow",
    "detached bungalow": "bungalow",
    "semi-detached bungalow": "bungalow",
    "cottage": "detached",
    "house": "house",
    "land": "land",
    "plot": "land",
    "park home": "park-home",
}


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")


def _norm_type(title: str | None) -> str | None:
    """Purplebricks titles read '3 bedroom semi-detached house' — parse the type words."""
    if not title:
        return None
    t = title.lower()
    t = re.sub(r"^\s*\d+\s*bedroom\s*", "", t)  # drop the "N bedroom" prefix
    t = t.strip()
    if t in _TYPE_MAP:
        return _TYPE_MAP[t]
    for phrase, mapped in _TYPE_MAP.items():
        if phrase in t:
            return mapped
    return None


def _extract_result_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not m:
        raise ValueError("purplebricks: __NEXT_DATA__ not found")
    return json.loads(m.group(1))["props"]["pageProps"]["ssrResultData"]


def _to_listing(prop: dict, mode: str) -> NormalizedListing:
    from ..research.geo import geocode_postcode

    features = prop.get("propertyFeatures") or {}
    postcode = prop.get("postcode") or None
    lat = lng = None
    if postcode:
        coords = geocode_postcode(postcode)
        if coords:
            lat, lng = coords

    image = prop.get("image") or {}
    image_url = image.get("mediumImage") or image.get("largeImage") or image.get("smallImage")

    status = "live"
    if prop.get("sold"):
        status = "removed"
    qualifier = prop.get("priceQualifier") or prop.get("priceText") or None
    if prop.get("underOffer"):
        qualifier = f"Under offer{' · ' + qualifier if qualifier else ''}"

    return NormalizedListing(
        portal=PORTAL,
        portal_id=str(prop["id"]),
        url=prop.get("listingUrl") or f"{BASE}/property-for-sale/{prop['id']}",
        mode=mode,
        price=prop.get("marketPrice"),
        price_qualifier=qualifier,
        status=status,
        address=prop.get("address") or "",
        postcode=postcode,
        lat=lat,
        lng=lng,
        beds=features.get("bedrooms"),
        baths=features.get("bathrooms"),
        property_type=_norm_type(prop.get("title")),
        description=prop.get("description") or prop.get("title") or "",
        image_urls=[image_url] if image_url else [],
        raw=None,
    )


class PurplebricksAdapter:
    portal = PORTAL

    def _town_slug(self, location: dict) -> str:
        """The label slug usually resolves; if a search yields nothing sensible the
        coordinates' admin district is a reliable fallback."""
        return _slug(location["label"])

    def search(self, profile, location: dict) -> list[NormalizedListing]:
        if profile.mode != "buy":
            log.info("purplebricks: rent not supported, skipping")
            return []

        slug = self._town_slug(location)
        params: dict = {
            "betasearch": "true",
            "soldOrLet": "false",
            "sortBy": 6,  # newest first
        }
        radius_km = location.get("radius_km")
        if radius_km:
            params["searchRadius"] = min(_MAX_RADIUS, max(1, round(radius_km * 0.621371)))
        if profile.min_price:
            params["priceFrom"] = profile.min_price
        if profile.max_price:
            params["priceTo"] = profile.max_price
        if profile.min_beds:
            params["bedroomsFrom"] = profile.min_beds
        if profile.max_beds:
            params["bedroomsTo"] = profile.max_beds

        def _run(town: str) -> tuple[list[NormalizedListing], int]:
            out: list[NormalizedListing] = []
            total = 0
            for page in range(1, MAX_PAGES + 1):
                if page > 1:
                    params["page"] = page
                resp = fetch(PORTAL, f"{BASE}/search/property-for-sale/{town}", params=params)
                data = _extract_result_data(resp.text)
                props = data.get("properties") or []
                total = int((data.get("metaData") or {}).get("totalItemCount") or 0)
                for prop in props:
                    try:
                        out.append(_to_listing(prop, profile.mode))
                    except Exception:
                        log.exception("purplebricks: failed to normalize %s", prop.get("id"))
                if page * 10 >= total or not props:
                    break
            return out, total

        results, total = _run(slug)
        # Empty label slug → retry with the coordinates' admin district
        if total == 0 and location.get("lat") is not None:
            from ..research.geo import reverse_district

            district = reverse_district(location["lat"], location["lng"])
            if district and _slug(district) != slug:
                log.info("purplebricks: %r empty, retrying district %r", slug, district)
                params.pop("page", None)
                results, total = _run(_slug(district))

        log.info("purplebricks: %s @ %s → %d listings", profile.mode, location["label"], len(results))
        return results
