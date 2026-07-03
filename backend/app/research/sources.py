"""Free UK data sources for neighbourhood research: Overpass (OSM), police.uk, postcodes.io.
All polite (identified UA, ~2s between Overpass calls) and cached in Meta by outcode+month."""
import json
import logging
import time
from datetime import date

import httpx
from sqlmodel import Session

from ..db import engine
from ..models import Meta

log = logging.getLogger("housespotter.research.sources")

USER_AGENT = "HouseSpotter/1.0 (personal property research tool)"
OVERPASS = "https://overpass-api.de/api/interpreter"
_last_overpass = 0.0


def _month_key() -> str:
    return date.today().strftime("%Y-%m")


def _cached(key: str) -> dict | None:
    with Session(engine) as session:
        row = session.get(Meta, key)
        if row and row.value:
            try:
                return json.loads(row.value)
            except json.JSONDecodeError:
                return None
    return None


def _store(key: str, data: dict) -> None:
    with Session(engine) as session:
        session.merge(Meta(key=key, value=json.dumps(data)))
        session.commit()


def outcode_centroid(outcode: str) -> tuple[float, float] | None:
    key = f"oc_centroid:{outcode}"
    cached = _cached(key)
    if cached:
        return cached["lat"], cached["lng"]
    try:
        resp = httpx.get(
            f"https://api.postcodes.io/outcodes/{outcode}",
            headers={"User-Agent": USER_AGENT}, timeout=15,
        )
        result = resp.json().get("result")
    except Exception:
        log.exception("postcodes.io outcode lookup failed for %s", outcode)
        return None
    if not result or result.get("latitude") is None:
        return None
    _store(key, {"lat": result["latitude"], "lng": result["longitude"]})
    return result["latitude"], result["longitude"]


def osm_amenity_counts(lat: float, lng: float, outcode: str) -> dict | None:
    """Counts of transport / amenity / green features near a point, via one Overpass query."""
    global _last_overpass
    key = f"osm:{outcode}:{_month_key()}"
    cached = _cached(key)
    if cached:
        return cached

    wait = _last_overpass + 2.0 - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_overpass = time.monotonic()

    query = f"""
[out:json][timeout:30];
(
  node["railway"="station"](around:2000,{lat},{lng});
) -> .stations;
(
  node["highway"="bus_stop"](around:800,{lat},{lng});
) -> .bus;
(
  node["shop"="supermarket"](around:1500,{lat},{lng});
  node["shop"="convenience"](around:1000,{lat},{lng});
) -> .shops;
(
  node["amenity"~"^(cafe|restaurant|pub|pharmacy|doctors|dentist)$"](around:1200,{lat},{lng});
) -> .amenities;
(
  node["amenity"="school"](around:1500,{lat},{lng});
  way["amenity"="school"](around:1500,{lat},{lng});
) -> .schools;
(
  way["leisure"~"^(park|nature_reserve|playground)$"](around:1500,{lat},{lng});
) -> .green;
.stations out count;
.bus out count;
.shops out count;
.amenities out count;
.schools out count;
.green out count;
"""
    try:
        resp = httpx.post(OVERPASS, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=60)
        elements = resp.json().get("elements", [])
    except Exception:
        log.exception("Overpass query failed for %s", outcode)
        return None
    counts = [int(e.get("tags", {}).get("total", 0)) for e in elements if e.get("type") == "count"]
    if len(counts) != 6:
        log.warning("Overpass returned %d count rows for %s", len(counts), outcode)
        return None
    data = {
        "stations": counts[0], "bus_stops": counts[1], "shops": counts[2],
        "amenities": counts[3], "schools": counts[4], "green_spaces": counts[5],
    }
    _store(key, data)
    return data


def outcodes_near(lat: float, lng: float, radius_km: float, cap: int = 24) -> list[str]:
    """All outcodes within radius_km of a point. postcodes.io caps its search radius at
    25km per query, so wider ranges are covered by sampling a ring grid of points."""
    import math

    points = [(lat, lng)]
    for ring_km in (radius_km * 0.5, radius_km):
        if ring_km < 8:
            continue
        for i in range(6):
            angle = math.radians(i * 60 + (30 if ring_km < radius_km else 0))
            dlat = (ring_km * math.cos(angle)) / 110.574
            dlng = (ring_km * math.sin(angle)) / (111.320 * math.cos(math.radians(lat)))
            points.append((lat + dlat, lng + dlng))

    query_radius = min(25000, int(radius_km * 1000))
    found: dict[str, float] = {}  # outcode -> distance from origin
    for p_lat, p_lng in points:
        try:
            resp = httpx.get(
                "https://api.postcodes.io/outcodes",
                params={"lon": p_lng, "lat": p_lat, "limit": 25, "radius": query_radius},
                headers={"User-Agent": USER_AGENT}, timeout=15,
            )
            results = resp.json().get("result") or []
        except Exception:
            log.exception("postcodes.io outcodes-near failed")
            continue
        for r in results:
            if r.get("latitude") is None:
                continue
            d = _haversine_km(lat, lng, r["latitude"], r["longitude"])
            if d <= radius_km and (r["outcode"] not in found or d < found[r["outcode"]]):
                found[r["outcode"]] = d
    return [oc for oc, _ in sorted(found.items(), key=lambda kv: kv[1])[:cap]]


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def crime_count(lat: float, lng: float, outcode: str) -> int | None:
    """Street-level crimes near a point in the most recent published month (police.uk)."""
    key = f"crime:{outcode}:{_month_key()}"
    cached = _cached(key)
    if cached:
        return cached["count"]
    try:
        resp = httpx.get(
            "https://data.police.uk/api/crimes-street/all-crime",
            params={"lat": lat, "lng": lng},
            headers={"User-Agent": USER_AGENT}, timeout=30,
        )
        if resp.status_code != 200:
            log.warning("police.uk returned %s for %s", resp.status_code, outcode)
            return None
        count = len(resp.json())
    except Exception:
        log.exception("police.uk query failed for %s", outcode)
        return None
    _store(key, {"count": count})
    return count
