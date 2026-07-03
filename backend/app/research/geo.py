"""Free geocoding helpers: Nominatim (place names) and postcodes.io (postcodes).
Results cached in Meta. Both services are free; be polite (identify, low volume)."""
import logging
import time

import httpx
from sqlmodel import Session

from ..db import engine
from ..models import Meta

log = logging.getLogger("housespotter.geo")

USER_AGENT = "HouseSpotter/1.0 (personal property search tool)"
_last_nominatim = 0.0


def _cache_get(key: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Meta, key)
        return row.value if row else None


def _cache_set(key: str, value: str) -> None:
    with Session(engine) as session:
        session.merge(Meta(key=key, value=value))
        session.commit()


def geocode_place(label: str) -> tuple[float, float] | None:
    """UK place name → (lat, lng). Cached; Nominatim limited to 1 req/s."""
    global _last_nominatim
    key = f"geo:{label.strip().lower()}"
    cached = _cache_get(key)
    if cached is not None:
        if cached == "":
            return None
        lat, lng = cached.split(",")
        return float(lat), float(lng)

    wait = _last_nominatim + 1.1 - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.monotonic()
    try:
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": label, "countrycodes": "gb", "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        results = resp.json()
    except Exception:
        log.exception("Nominatim lookup failed for %r", label)
        return None
    if not results:
        _cache_set(key, "")
        log.warning("Could not geocode %r", label)
        return None
    lat, lng = float(results[0]["lat"]), float(results[0]["lon"])
    _cache_set(key, f"{lat},{lng}")
    return lat, lng


def reverse_outcode(lat: float, lng: float) -> str | None:
    """(lat, lng) → nearest outcode (e.g. 'GU15') via postcodes.io. Cached to 3dp."""
    key = f"outcode:{round(lat, 3)},{round(lng, 3)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached or None
    try:
        resp = httpx.get(
            "https://api.postcodes.io/postcodes",
            params={"lon": lng, "lat": lat, "limit": 1, "radius": 2000},  # max radius
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        data = resp.json()
    except Exception:
        log.exception("postcodes.io reverse lookup failed")
        return None
    results = data.get("result") or []
    if not results:
        # Rural spot >2km from any postcode: try the nearest outcode centroid instead
        try:
            resp = httpx.get(
                "https://api.postcodes.io/outcodes",
                params={"lon": lng, "lat": lat, "limit": 1, "radius": 25000},
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            results = resp.json().get("result") or []
        except Exception:
            log.exception("postcodes.io outcode fallback failed")
            return None
    outcode = results[0]["outcode"] if results else None
    if outcode:  # never cache misses — transient API issues would stick forever
        _cache_set(key, outcode)
    return outcode
