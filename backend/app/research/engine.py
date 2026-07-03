"""Neighbourhood research over saved AreaSearches.

Each AreaSearch owns its result set (AreaResult rows) and its own run status, so
multiple searches coexist per profile. source='profile' searches mirror the profile's
locations (kept in sync via ensure_profile_search); source='custom' are user searches.
"""
import json
import logging
import re
import statistics

from sqlmodel import Session, select

from ..config import settings
from ..db import engine as db_engine, session_scope
from ..llm.client import cached_json_call, llm_available, make_cache_key
from ..models import AreaResult, AreaSearch, Listing, Meta, Property, SearchProfile, utcnow
from .sources import crime_count, osm_amenity_counts, outcode_centroid, outcodes_near

log = logging.getLogger("housespotter.research")

MIN_LISTINGS = 3  # legacy fallback (search with no locations): min listings per outcode
OUTCODE_RE = re.compile(r"^[A-Za-z]{1,2}\d[\dA-Za-z]?$")
MAX_CANDIDATES = 30

DEFAULT_WEIGHTS = {"transport": 2, "safety": 2, "amenities": 2, "green": 1, "schools": 1, "affordability": 2}


# --- Run status (persisted per search so the UI can poll it) ---

def _status_key(search_id: int) -> str:
    return f"research_status:search:{search_id}"


def set_status(search_id: int, **fields) -> None:
    status = get_status(search_id)
    status.update(fields)
    with Session(db_engine) as session:
        session.merge(Meta(key=_status_key(search_id), value=json.dumps(status)))
        session.commit()


def get_status(search_id: int) -> dict:
    with Session(db_engine) as session:
        row = session.get(Meta, _status_key(search_id))
    if row and row.value:
        try:
            status = json.loads(row.value)
        except json.JSONDecodeError:
            return {"state": "idle"}
        if status.get("state") == "running" and status.get("started_at"):
            from datetime import datetime, timedelta, timezone

            try:
                started = datetime.fromisoformat(status["started_at"])
                if datetime.now(timezone.utc) - started > timedelta(minutes=30):
                    status["state"] = "error"
                    status["error"] = "Research was interrupted — run it again."
            except ValueError:
                pass
        return status
    return {"state": "idle"}


# --- Profile-linked search lifecycle ---

def ensure_profile_search(profile_id: int) -> AreaSearch | None:
    """Create or sync the pinned source='profile' search from the profile's locations."""
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile:
            return None
        locations = [
            {
                "label": l.get("label"), "lat": l.get("lat"), "lng": l.get("lng"),
                # widen the match radius a little for discovery, so neighbours show up
                "radius_km": max((l.get("radius_km") or 8) * 1.5, 10),
            }
            for l in (profile.locations or [])
            if l.get("lat") is not None
        ]
        labels = " + ".join(l["label"] for l in locations if l.get("label")) or profile.name
        name = f"From profile: {labels}"

        search = session.exec(
            select(AreaSearch).where(
                AreaSearch.profile_id == profile_id, AreaSearch.source == "profile"
            )
        ).first()
        if search is None:
            search = AreaSearch(profile_id=profile_id, name=name, source="profile", locations=locations)
            session.add(search)
        elif search.locations != locations or search.name != name:
            search.locations = locations
            search.name = name
            if search.last_run_at is not None:
                search.stale = True  # results were computed for the old locations
            session.add(search)
        session.commit()
        session.refresh(search)
        return search


# --- Running a search ---

def run_area_search(search_id: int) -> int:
    try:
        return _run(search_id)
    except Exception as exc:
        log.exception("Research failed for search %s", search_id)
        set_status(search_id, state="error", error=str(exc), finished_at=utcnow().isoformat())
        return 0


def _candidates_from_locations(locations: list) -> list[str]:
    found: list[str] = []
    for loc in locations:
        for oc in outcodes_near(loc["lat"], loc["lng"], loc.get("radius_km") or 10, cap=MAX_CANDIDATES):
            if oc not in found:
                found.append(oc)
    return found[:MAX_CANDIDATES]


def _fallback_candidates(session: Session, mode: str) -> list[str]:
    """Legacy searches with no stored locations: outcodes where scraped listings exist."""
    counts: dict[str, int] = {}
    for listing in session.exec(
        select(Listing).where(Listing.mode == mode, Listing.status != "removed")
    ).all():
        prop = session.get(Property, listing.property_id)
        if prop and prop.outcode:
            counts[prop.outcode] = counts.get(prop.outcode, 0) + 1
    return sorted([oc for oc, n in counts.items() if n >= MIN_LISTINGS])


def _listing_prices_for(session: Session, mode: str, outcodes: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {oc: [] for oc in outcodes}
    for listing in session.exec(
        select(Listing).where(Listing.mode == mode, Listing.status != "removed")
    ).all():
        prop = session.get(Property, listing.property_id)
        if prop and prop.outcode in out and listing.price:
            out[prop.outcode].append(listing.price)
    return out


def _run(search_id: int) -> int:
    with session_scope() as session:
        search = session.get(AreaSearch, search_id)
        if not search:
            return 0
        profile = session.get(SearchProfile, search.profile_id)
        if not profile:
            return 0
        mode = profile.mode
        max_price = profile.max_price
        weights = {**DEFAULT_WEIGHTS, **{k: v for k, v in (profile.qol_weights or {}).items() if v is not None}}
        brief = profile.brief
        locations = search.locations or []
        search_name = search.name

    set_status(
        search_id, state="running", progress="finding areas",
        started_at=utcnow().isoformat(), error=None, finished_at=None,
    )

    with session_scope() as session:
        if locations:
            outcodes = _candidates_from_locations(locations)
        else:
            outcodes = _fallback_candidates(session, mode)
        prices_by_outcode = _listing_prices_for(session, mode, outcodes)

    if not outcodes:
        set_status(
            search_id, state="error", finished_at=utcnow().isoformat(),
            error="No candidate areas found for this search.",
        )
        return 0
    log.info("Research '%s': %d areas: %s", search_name, len(outcodes), outcodes)

    raw: dict[str, dict] = {}
    for i, outcode in enumerate(outcodes):
        set_status(search_id, state="running", progress=f"{i + 1}/{len(outcodes)} areas")
        centroid = outcode_centroid(outcode)
        if not centroid:
            continue
        lat, lng = centroid
        osm = osm_amenity_counts(lat, lng, outcode) or {}
        crimes = crime_count(lat, lng, outcode)
        prices = prices_by_outcode.get(outcode) or []
        raw[outcode] = {
            "lat": lat, "lng": lng,
            "median_price": int(statistics.median(prices)) if prices else None,
            "listing_count": len(prices),
            "crimes_month": crimes,
            **osm,
        }

    if not raw:
        set_status(search_id, state="error", error="No area data could be fetched", finished_at=utcnow().isoformat())
        return 0

    set_status(search_id, state="running", progress="scoring & writing profiles")

    def norm(key: str, invert: bool = False) -> dict[str, float]:
        values = {oc: m.get(key) for oc, m in raw.items() if m.get(key) is not None}
        if len(values) < 2:
            return {}
        low, high = min(values.values()), max(values.values())
        span = (high - low) or 1
        return {oc: (high - v) / span if invert else (v - low) / span for oc, v in values.items()}

    transport_a = norm("stations")
    transport_b = norm("bus_stops")
    amenities_a = norm("shops")
    amenities_b = norm("amenities")
    green_n = norm("green_spaces")
    schools_n = norm("schools")
    safety_n = norm("crimes_month", invert=True)
    afford_n = norm("median_price", invert=True)

    scored: dict[str, dict] = {}
    for oc in raw:
        subs = {
            "transport": round(0.7 * transport_a.get(oc, 0.5) + 0.3 * transport_b.get(oc, 0.5), 2),
            "amenities": round(0.5 * amenities_a.get(oc, 0.5) + 0.5 * amenities_b.get(oc, 0.5), 2),
            "green": round(green_n.get(oc, 0.5), 2),
            "schools": round(schools_n.get(oc, 0.5), 2),
            "safety": round(safety_n.get(oc, 0.5), 2),
            "affordability": round(afford_n.get(oc, 0.5), 2),
        }
        total_w = sum(weights.values()) or 1
        subs["total"] = round(sum(subs[k] * weights.get(k, 1) for k in DEFAULT_WEIGHTS) / total_w, 3)
        scored[oc] = subs

    # Replace this search's results (other searches keep theirs)
    with session_scope() as session:
        for old in session.exec(
            select(AreaResult).where(AreaResult.area_search_id == search_id)
        ).all():
            if old.code not in scored:
                session.delete(old)
        session.commit()

    count = 0
    for oc, subs in scored.items():
        metrics = raw[oc]
        stats = {
            "listing_count": metrics["listing_count"],
            "median_price": metrics["median_price"],
            "in_budget": (metrics["median_price"] <= max_price) if (metrics["median_price"] and max_price) else None,
        }
        narrative = _narrative(oc, subs, metrics, stats, mode, max_price, brief)
        with session_scope() as session:
            existing = session.exec(
                select(AreaResult).where(
                    AreaResult.area_search_id == search_id, AreaResult.code == oc
                )
            ).first()
            area = existing or AreaResult(area_search_id=search_id, code=oc)
            area.name = oc
            area.lat, area.lng = metrics["lat"], metrics["lng"]
            area.metrics = metrics
            area.scores = subs
            area.narrative = narrative
            area.listing_stats = stats
            area.refreshed_at = utcnow()
            session.add(area)
            session.commit()
            count += 1

    with session_scope() as session:
        search = session.get(AreaSearch, search_id)
        if search:
            search.last_run_at = utcnow()
            search.stale = False
            session.add(search)
            session.commit()

    set_status(
        search_id, state="done", progress=None,
        areas=count, finished_at=utcnow().isoformat(),
    )
    log.info("Research complete for search %s: %d areas", search_id, count)
    return count


NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {"narrative": {"type": "string"}},
    "required": ["narrative"],
    "additionalProperties": False,
}

NARRATIVE_SYSTEM = (
    "You are a knowledgeable UK estate agent writing a short, honest area profile for a "
    "house-hunter. Write 2-3 sentences: character of the area, standout strengths from the "
    "data, any weaknesses worth knowing, and — when price data is present — how local prices "
    "sit against their budget. Ground every claim in the supplied data; if listing data is "
    "absent, don't invent prices. No hedging boilerplate, no bullet points."
)


def _narrative(outcode, subs, metrics, stats, mode, max_price, brief) -> str:
    if llm_available():
        payload = json.dumps({
            "outcode": outcode, "sub_scores_0to1": subs, "raw_metrics": metrics,
            "listings": stats, "mode": mode, "user_budget": max_price,
            "what_user_wants": brief or "not specified",
        }, ensure_ascii=False)
        cache_key = make_cache_key("area-narrative", payload, settings.model_research)
        result = cached_json_call(
            cache_key, model=settings.model_research, system=NARRATIVE_SYSTEM,
            user_content=payload, schema=NARRATIVE_SCHEMA, max_tokens=400,
        )
        if result and result.get("narrative"):
            return result["narrative"]

    strong = [k for k, v in subs.items() if k != "total" and v >= 0.7]
    weak = [k for k, v in subs.items() if k != "total" and v <= 0.3]
    if stats["listing_count"]:
        parts = [f"{outcode}: {stats['listing_count']} matching listings, median £{stats['median_price']:,}."]
    else:
        parts = [f"{outcode}: no scanned listings here yet."]
    if strong:
        parts.append("Strong on " + ", ".join(strong) + ".")
    if weak:
        parts.append("Weaker on " + ", ".join(weak) + ".")
    return " ".join(parts)
