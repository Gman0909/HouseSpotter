"""UK train stations: one country-wide OSM fetch cached in the DB, nearest-station
lookups by haversine, and walking times via ORS (estimates until routed).

Deliberately generic: the scoring criterion is "close to train links" — a named
station is never a parameter (that's what per-user Milestones are for)."""
import logging
import math

import httpx
from sqlmodel import Session, select

from ..db import engine, session_scope
from ..models import Property, PropertyStation, TrainStation, utcnow

log = logging.getLogger("housespotter.stations")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# GB heavy-rail stations; excludes metro/underground so "train links" means trains
OVERPASS_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="GB"][admin_level=2]->.uk;
nwr["railway"="station"]["station"!~"subway|funicular|monorail|miniature|light_rail"](area.uk);
out center tags;
"""

WALK_KMH = 4.7
ROUTE_FACTOR = 1.3  # crow-flies → street-network fudge for estimates
MAX_STATION_KM = 15.0  # beyond this we just report the distance, no walk framing

_station_cache: list[tuple[int, str, float, float]] | None = None  # (id, name, lat, lng)


def refresh_stations() -> int:
    """Fetch all GB rail stations from Overpass into the TrainStation table.
    Cheap to re-run (monthly); the table is replaced atomically on success."""
    try:
        from .sources import USER_AGENT

        resp = httpx.post(
            OVERPASS_URL,
            data={"data": OVERPASS_QUERY},
            headers={"User-Agent": USER_AGENT},  # Overpass 406s anonymous default UAs
            timeout=240,
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception:
        log.exception("Overpass station fetch failed; keeping existing station table")
        return 0

    rows = []
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name")
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        if name and lat is not None and lng is not None:
            rows.append((name, lat, lng))
    if len(rows) < 500:  # sanity: GB has ~2,800 stations; a tiny result means a bad fetch
        log.warning("Overpass returned only %d stations; keeping existing table", len(rows))
        return 0

    global _station_cache
    with session_scope() as session:
        from sqlalchemy import delete as sa_delete

        session.exec(sa_delete(PropertyStation))  # station ids change on refresh
        session.exec(sa_delete(TrainStation))
        for name, lat, lng in rows:
            session.add(TrainStation(name=name, lat=lat, lng=lng))
        session.commit()
    _station_cache = None
    log.info("Station table refreshed: %d stations", len(rows))
    return len(rows)


def ensure_stations() -> bool:
    """Load (and if empty, fetch) the station table. Returns availability."""
    global _station_cache
    if _station_cache is not None:
        return len(_station_cache) > 0
    with Session(engine) as session:
        stations = session.exec(select(TrainStation)).all()
    if not stations:
        if refresh_stations():
            with Session(engine) as session:
                stations = session.exec(select(TrainStation)).all()
    _station_cache = [(s.id, s.name, s.lat, s.lng) for s in stations]
    return len(_station_cache) > 0


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_stations(lat: float, lng: float, limit: int = 2) -> list[tuple[int, str, float]]:
    """[(station_id, name, crow_km)] nearest first. Cheap in-memory scan."""
    if not ensure_stations():
        return []
    scored = [
        (sid, name, _haversine_km(lat, lng, slat, slng))
        for sid, name, slat, slng in _station_cache
    ]
    scored.sort(key=lambda t: t[2])
    return scored[:limit]


def _estimate_walk_minutes(km: float) -> float:
    return km * ROUTE_FACTOR / WALK_KMH * 60


def station_info(prop: Property) -> dict | None:
    """Nearest station for a property: cached ORS walk time if we have it, estimate
    otherwise. Never calls ORS (scoring-path safe)."""
    if prop.lat is None or prop.lng is None:
        return None
    nearest = nearest_stations(prop.lat, prop.lng, limit=1)
    if not nearest:
        return None
    sid, name, crow_km = nearest[0]
    if crow_km > MAX_STATION_KM:
        return {"name": name, "km": round(crow_km, 1), "walk_minutes": None, "provider": "estimate"}
    with Session(engine) as session:
        row = session.exec(
            select(PropertyStation).where(
                PropertyStation.property_id == prop.id, PropertyStation.station_id == sid
            )
        ).first()
        if row and row.provider == "ors" and row.walk_minutes is not None:
            return {"name": name, "km": row.km, "walk_minutes": row.walk_minutes, "provider": "ors"}
    return {
        "name": name,
        "km": round(crow_km, 1),
        "walk_minutes": round(_estimate_walk_minutes(crow_km), 1),
        "provider": "estimate",
    }


def compute_property_station(property_id: int, force: bool = False) -> dict | None:
    """Route the walk to the nearest station via ORS and cache it. Falls back to the
    estimate silently when ORS is unavailable."""
    from .travel import _ors_matrix

    with session_scope() as session:
        prop = session.get(Property, property_id)
        if not prop or prop.lat is None:
            return None
        nearest = nearest_stations(prop.lat, prop.lng, limit=1)
        if not nearest:
            return None
        sid, name, crow_km = nearest[0]
        if crow_km > MAX_STATION_KM:
            return station_info(prop)
        existing = session.exec(
            select(PropertyStation).where(
                PropertyStation.property_id == property_id, PropertyStation.station_id == sid
            )
        ).first()
        if existing and existing.provider == "ors" and not force:
            return {"name": name, "km": existing.km, "walk_minutes": existing.walk_minutes, "provider": "ors"}
        station = session.get(TrainStation, sid)
        result = _ors_matrix("foot-walking", [(prop.lat, prop.lng)], [(station.lat, station.lng)])
        durations = result[0] if result else None
        if not durations or durations[0][0] is None:
            return station_info(prop)
        minutes = round(durations[0][0] / 60, 1)
        distances = result[1]
        km = round(distances[0][0] / 1000, 2) if distances and distances[0][0] is not None else round(crow_km, 2)
        if existing:
            existing.walk_minutes = minutes
            existing.km = km
            existing.provider = "ors"
            existing.computed_at = utcnow()
            session.add(existing)
        else:
            session.add(PropertyStation(
                property_id=property_id, station_id=sid,
                walk_minutes=minutes, km=km, provider="ors",
            ))
        session.commit()
        return {"name": name, "km": km, "walk_minutes": minutes, "provider": "ors"}


def station_walk_map(session: Session, property_ids: list[int]) -> dict[int, dict | None]:
    """Bulk station_info for feed cards — one PropertyStation query, in-memory nearest."""
    if not ensure_stations():
        return {p: None for p in property_ids}
    cached: dict[tuple[int, int], PropertyStation] = {}
    for row in session.exec(
        select(PropertyStation).where(PropertyStation.provider == "ors")
    ).all():
        cached[(row.property_id, row.station_id)] = row
    out: dict[int, dict | None] = {}
    for pid in property_ids:
        prop = session.get(Property, pid)
        if not prop or prop.lat is None:
            out[pid] = None
            continue
        nearest = nearest_stations(prop.lat, prop.lng, limit=1)
        if not nearest:
            out[pid] = None
            continue
        sid, name, crow_km = nearest[0]
        if crow_km > MAX_STATION_KM:
            out[pid] = None
            continue
        row = cached.get((pid, sid))
        if row and row.walk_minutes is not None:
            out[pid] = {"name": name, "walk_minutes": row.walk_minutes, "provider": "ors"}
        else:
            out[pid] = {
                "name": name,
                "walk_minutes": round(_estimate_walk_minutes(crow_km), 1),
                "provider": "estimate",
            }
    return out


def refresh_station_batch() -> None:
    """Nightly: route walks for live properties missing an ORS station time.
    Grouped by shared nearest station so each ORS matrix call covers many properties."""
    from ..models import Listing
    from .travel import _ors_matrix

    with Session(engine) as session:
        if not ensure_stations():
            return
        live_ids = {
            l.property_id
            for l in session.exec(select(Listing).where(Listing.status != "removed")).all()
        }
        have = {
            (r.property_id, r.station_id)
            for r in session.exec(
                select(PropertyStation).where(PropertyStation.provider == "ors")
            ).all()
        }
        by_station: dict[int, list[Property]] = {}
        for pid in live_ids:
            prop = session.get(Property, pid)
            if not prop or prop.lat is None:
                continue
            nearest = nearest_stations(prop.lat, prop.lng, limit=1)
            if not nearest or nearest[0][2] > MAX_STATION_KM:
                continue
            sid = nearest[0][0]
            if (pid, sid) not in have:
                by_station.setdefault(sid, []).append(prop)

    total = 0
    for sid, props in by_station.items():
        with session_scope() as session:
            station = session.get(TrainStation, sid)
            if not station:
                continue
            for chunk_start in range(0, len(props), 40):
                chunk = props[chunk_start : chunk_start + 40]
                result = _ors_matrix(
                    "foot-walking",
                    [(p.lat, p.lng) for p in chunk],
                    [(station.lat, station.lng)],
                )
                durations = result[0] if result else None
                if not durations:
                    continue
                distances = result[1]
                for i, prop in enumerate(chunk):
                    seconds = durations[i][0] if i < len(durations) else None
                    if seconds is None:
                        continue
                    metres = distances[i][0] if distances and i < len(distances) else None
                    session.add(PropertyStation(
                        property_id=prop.id, station_id=sid,
                        walk_minutes=round(seconds / 60, 1),
                        km=round(metres / 1000, 2) if metres is not None else None,
                        provider="ors",
                    ))
                    total += 1
                session.commit()
    if total:
        log.info("Station batch: routed %d property→station walks", total)
