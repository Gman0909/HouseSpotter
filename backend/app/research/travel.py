"""Travel times from properties to the user's Milestones, and the Milestone Access Score.

Real routing via OpenRouteService (car/cycle/walk), cached forever in TravelTime.
When ORS is unavailable (no key, quota, unroutable) we fall back to clearly-labelled
crow-flies estimates computed on the fly — never stored, so real data replaces them
as soon as it can be fetched.
"""
import logging
import math

import httpx
from sqlmodel import Session, select

from ..config import settings
from ..db import engine, session_scope
from ..models import Listing, Milestone, Property, TravelTime, utcnow

log = logging.getLogger("housespotter.travel")

ORS_MODES = {"car": "driving-car", "cycle": "cycling-regular", "walk": "foot-walking"}
EST_SPEED_KMH = {"car": 45.0, "cycle": 14.0, "walk": 4.5}
EST_OVERHEAD_MIN = {"car": 4.0, "cycle": 2.0, "walk": 0.0}
ROUTE_FACTOR = 1.3  # crow-flies → typical road distance

# Access-score curve (car minutes): ≤ BEST → 1.0, ≥ WORST → 0.0 for nearby milestones.
# Distant milestones use a longer curve anchored to the best achievable time from the
# current property pool (see _curve), so they still differentiate properties.
BEST_MIN, WORST_MIN = 8.0, 45.0
BASE_SPAN = WORST_MIN - BEST_MIN  # 37 min


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def estimate(lat1, lng1, lat2, lng2, mode: str) -> tuple[float, float]:
    """(minutes, km) rough estimate from crow-flies distance."""
    km = _haversine_km(lat1, lng1, lat2, lng2) * ROUTE_FACTOR
    minutes = EST_OVERHEAD_MIN[mode] + km / EST_SPEED_KMH[mode] * 60
    return round(minutes, 1), round(km, 1)


def _ors_matrix(profile: str, sources: list[tuple[float, float]], destinations: list[tuple[float, float]]):
    """ORS matrix: rows of (seconds, metres) per source×destination. None on failure."""
    if not settings.ors_api_key:
        return None
    locations = [[lng, lat] for lat, lng in sources] + [[lng, lat] for lat, lng in destinations]
    try:
        resp = httpx.post(
            f"https://api.openrouteservice.org/v2/matrix/{profile}",
            headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
            json={
                "locations": locations,
                "sources": list(range(len(sources))),
                "destinations": list(range(len(sources), len(locations))),
                "metrics": ["duration", "distance"],
            },
            timeout=45,
        )
        if resp.status_code != 200:
            log.warning("ORS matrix %s returned %s: %s", profile, resp.status_code, resp.text[:150])
            return None
        data = resp.json()
        return data.get("durations"), data.get("distances")
    except Exception:
        log.exception("ORS matrix call failed")
        return None


def compute_property_travel(property_id: int, user_id: int, force: bool = False) -> list[dict]:
    """Travel rows for one property to the user's milestones, fetching+caching ORS
    results for missing (property, milestone, mode) combos. Estimates fill gaps."""
    with session_scope() as session:
        prop = session.get(Property, property_id)
        milestones = session.exec(
            select(Milestone).where(Milestone.user_id == user_id).order_by(Milestone.id)
        ).all()
        if not prop or not milestones:
            return []
        cached = {
            (t.milestone_id, t.mode): {"minutes": t.minutes, "km": t.km, "provider": t.provider}
            for t in session.exec(
                select(TravelTime).where(TravelTime.property_id == property_id)
            ).all()
        }
        prop_coords = (prop.lat, prop.lng) if prop.lat is not None else None
        ms_data = [
            {"id": m.id, "label": m.label, "weight": m.weight, "lat": m.lat, "lng": m.lng}
            for m in milestones
        ]

    # Fetch what's missing, one matrix call per mode
    if prop_coords and settings.ors_api_key:
        for mode, ors_profile in ORS_MODES.items():
            missing = [m for m in ms_data if force or (m["id"], mode) not in cached]
            if not missing:
                continue
            result = _ors_matrix(ors_profile, [prop_coords], [(m["lat"], m["lng"]) for m in missing])
            if not result:
                continue
            durations, distances = result
            with session_scope() as session:
                for i, m in enumerate(missing):
                    secs = durations[0][i] if durations else None
                    metres = distances[0][i] if distances else None
                    minutes = round(secs / 60, 1) if secs is not None else None
                    km = round(metres / 1000, 1) if metres is not None else None
                    row = session.exec(
                        select(TravelTime).where(
                            TravelTime.property_id == property_id,
                            TravelTime.milestone_id == m["id"],
                            TravelTime.mode == mode,
                        )
                    ).first() or TravelTime(property_id=property_id, milestone_id=m["id"], mode=mode)
                    row.minutes = minutes
                    row.km = km
                    row.provider = "ors"
                    row.computed_at = utcnow()
                    session.add(row)
                    session.commit()
                    cached[(m["id"], mode)] = {"minutes": minutes, "km": km, "provider": "ors"}

    # Assemble: cached rows first, estimates for anything else
    out = []
    for m in ms_data:
        modes: dict[str, dict] = {}
        for mode in ORS_MODES:
            row = cached.get((m["id"], mode))
            if row and row["provider"] == "ors" and row["minutes"] is not None:
                modes[mode] = row
            elif prop_coords:
                minutes, km = estimate(prop_coords[0], prop_coords[1], m["lat"], m["lng"], mode)
                modes[mode] = {"minutes": minutes, "km": km, "provider": "estimate"}
            else:
                modes[mode] = {"minutes": None, "km": None, "provider": "none"}
        out.append({**m, "modes": modes})
    return out


# --- Access score ---

def _curve(minutes: float, baseline: float | None = None) -> float:
    """Convenience 0-1. For milestones whose best-achievable time (baseline) exceeds
    the nearby threshold, the curve starts at the baseline and its span grows with it —
    a property at 'as good as it gets' scores 1.0 even for a distant place, and the
    penalty accrues over a proportionally longer range."""
    if baseline is not None and baseline > BEST_MIN:
        lo = baseline
        span = max(BASE_SPAN, baseline)
    else:
        lo = BEST_MIN
        span = BASE_SPAN
    return max(0.0, min(1.0, (lo + span - minutes) / span))


def _milestone_baselines(session: Session) -> dict[int, float]:
    """Best cached car time per milestone across all properties (routing-quality only)."""
    baselines: dict[int, float] = {}
    for t in session.exec(
        select(TravelTime).where(
            TravelTime.mode == "car",
            TravelTime.provider == "ors",
            TravelTime.minutes != None,  # noqa: E711
        )
    ).all():
        if t.milestone_id not in baselines or t.minutes < baselines[t.milestone_id]:
            baselines[t.milestone_id] = t.minutes
    return baselines


def access_scores(session: Session, property_ids: list[int], user_id: int) -> dict[int, int | None]:
    """Milestone Access Score per property (0-100) against the user's milestones,
    from cached car times where available, estimates otherwise."""
    milestones = session.exec(select(Milestone).where(Milestone.user_id == user_id)).all()
    if not milestones:
        return {pid: None for pid in property_ids}
    rows = session.exec(
        select(TravelTime).where(
            TravelTime.mode == "car",
            TravelTime.property_id.in_(property_ids),  # type: ignore[attr-defined]
        )
    ).all()
    cached = {(t.property_id, t.milestone_id): t.minutes for t in rows if t.provider == "ors"}
    baselines = _milestone_baselines(session)

    scores: dict[int, int | None] = {}
    for pid in property_ids:
        prop = session.get(Property, pid)
        if not prop or prop.lat is None:
            scores[pid] = None
            continue
        total_w = acc = 0.0
        for m in milestones:
            minutes = cached.get((pid, m.id))
            if minutes is None:
                minutes, _ = estimate(prop.lat, prop.lng, m.lat, m.lng, "car")
            acc += m.weight * _curve(minutes, baselines.get(m.id))
            total_w += m.weight
        scores[pid] = round(100 * acc / total_w) if total_w else None
    return scores


def access_score_single(property_id: int, user_id: int | None) -> tuple[int | None, float | None]:
    """(score, weighted avg car minutes) for one property — used by match scoring."""
    with Session(engine) as session:
        milestones = session.exec(select(Milestone).where(Milestone.user_id == user_id)).all()
        if not milestones:
            return None, None
        prop = session.get(Property, property_id)
        if not prop or prop.lat is None:
            return None, None
        rows = session.exec(
            select(TravelTime).where(
                TravelTime.property_id == property_id, TravelTime.mode == "car"
            )
        ).all()
        cached = {t.milestone_id: t.minutes for t in rows if t.provider == "ors" and t.minutes is not None}
        baselines = _milestone_baselines(session)
        total_w = acc = mins_acc = 0.0
        for m in milestones:
            minutes = cached.get(m.id)
            if minutes is None:
                minutes, _ = estimate(prop.lat, prop.lng, m.lat, m.lng, "car")
            acc += m.weight * _curve(minutes, baselines.get(m.id))
            mins_acc += m.weight * minutes
            total_w += m.weight
        return round(100 * acc / total_w), round(mins_acc / total_w)


# --- Nightly batch: real car times for all live properties ---

def refresh_travel_batch() -> None:
    """Fill the car-mode cache for every property with a live listing (chunked matrix
    calls, well inside the ORS free quota). Runs nightly."""
    if not settings.ors_api_key:
        return
    with session_scope() as session:
        milestones = session.exec(select(Milestone)).all()
        if not milestones:
            return
        live_prop_ids = {
            l.property_id
            for l in session.exec(select(Listing).where(Listing.status != "removed")).all()
        }
        done = {
            (t.property_id, t.milestone_id)
            for t in session.exec(
                select(TravelTime).where(TravelTime.mode == "car", TravelTime.provider == "ors")
            ).all()
        }
        todo = []
        for pid in live_prop_ids:
            if all((pid, m.id) in done for m in milestones):
                continue
            prop = session.get(Property, pid)
            if prop and prop.lat is not None:
                todo.append((pid, prop.lat, prop.lng))
        ms = [(m.id, m.lat, m.lng) for m in milestones]

    if not todo:
        return
    log.info("Travel batch: %d properties × %d milestones (car)", len(todo), len(ms))
    CHUNK = 40
    for start in range(0, len(todo), CHUNK):
        chunk = todo[start : start + CHUNK]
        result = _ors_matrix("driving-car", [(lat, lng) for _, lat, lng in chunk],
                             [(lat, lng) for _, lat, lng in ms])
        if not result:
            break  # quota or outage — try again next night
        durations, distances = result
        with session_scope() as session:
            for i, (pid, _, _) in enumerate(chunk):
                for j, (mid, _, _) in enumerate(ms):
                    row = session.exec(
                        select(TravelTime).where(
                            TravelTime.property_id == pid,
                            TravelTime.milestone_id == mid,
                            TravelTime.mode == "car",
                        )
                    ).first() or TravelTime(property_id=pid, milestone_id=mid, mode="car")
                    secs = durations[i][j] if durations else None
                    metres = distances[i][j] if distances else None
                    row.minutes = round(secs / 60, 1) if secs is not None else None
                    row.km = round(metres / 1000, 1) if metres is not None else None
                    row.provider = "ors"
                    row.computed_at = utcnow()
                    session.add(row)
            session.commit()
    log.info("Travel batch complete")
