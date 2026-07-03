from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import AreaResult, AreaSearch, MatchScore, Meta, Property, SearchProfile

router = APIRouter(prefix="/api/areas", tags=["areas"], dependencies=[Depends(require_user)])

MILES_TO_KM = 1.60934


def _search_payload(search: AreaSearch, session: Session) -> dict:
    from ..research.engine import get_status

    result_count = len(session.exec(
        select(AreaResult).where(AreaResult.area_search_id == search.id)
    ).all())
    return {
        **search.model_dump(),
        "status": get_status(search.id),
        "result_count": result_count,
    }


@router.get("/searches")
def list_searches(profile_id: int, session: Session = Depends(get_session)):
    from ..research.engine import ensure_profile_search

    ensure_profile_search(profile_id)
    searches = session.exec(
        select(AreaSearch).where(AreaSearch.profile_id == profile_id)
    ).all()
    # Pinned profile search first, then newest custom searches
    searches.sort(key=lambda s: (s.source != "profile", -(s.id or 0)))
    return [_search_payload(s, session) for s in searches]


@router.post("/searches")
def create_search(body: dict, background: BackgroundTasks, session: Session = Depends(get_session)):
    """Create (or reuse) a custom saved search and start running it."""
    profile_id = body.get("profile_id")
    location = (body.get("location") or "").strip()
    if not profile_id or not location:
        raise HTTPException(422, "profile_id and location required")
    if not session.get(SearchProfile, profile_id):
        raise HTTPException(404, "profile not found")
    radius_km = round(min(40.0, float(body.get("radius_miles") or 15)) * MILES_TO_KM, 1)

    from ..research.engine import OUTCODE_RE, get_status, run_area_search
    from ..research.geo import geocode_place
    from ..research.sources import outcode_centroid

    if OUTCODE_RE.match(location):
        coords = outcode_centroid(location.upper())
        label = location.upper()
    else:
        coords = geocode_place(location)
        label = location
    if not coords:
        raise HTTPException(422, f"Couldn't find '{location}' — try a town name or postcode district")
    loc = {"label": label, "lat": coords[0], "lng": coords[1], "radius_km": radius_km}

    # Reuse an equivalent existing search rather than piling up duplicates
    existing = session.exec(
        select(AreaSearch).where(
            AreaSearch.profile_id == profile_id, AreaSearch.source == "custom"
        )
    ).all()
    search = None
    for s in existing:
        if s.locations == [loc]:
            search = s
            break
    if search is None:
        search = AreaSearch(
            profile_id=profile_id,
            name=f"{label} · {int(round(radius_km / MILES_TO_KM))} mi",
            source="custom",
            locations=[loc],
        )
        session.add(search)
        session.commit()
        session.refresh(search)

    if get_status(search.id).get("state") == "running":
        raise HTTPException(409, "This search is already running")
    from ..models import utcnow
    from ..research.engine import set_status

    set_status(search.id, state="running", progress="starting", started_at=utcnow().isoformat(),
               error=None, finished_at=None)
    background.add_task(run_area_search, search.id)
    return _search_payload(search, session)


@router.post("/searches/{search_id}/run")
def rerun_search(search_id: int, background: BackgroundTasks, session: Session = Depends(get_session)):
    search = session.get(AreaSearch, search_id)
    if not search:
        raise HTTPException(404)
    from ..models import utcnow
    from ..research.engine import ensure_profile_search, get_status, run_area_search, set_status

    if get_status(search_id).get("state") == "running":
        raise HTTPException(409, "This search is already running")
    if search.source == "profile":
        ensure_profile_search(search.profile_id)  # pick up latest profile locations
        session.refresh(search)
    if not search.locations:
        raise HTTPException(422, "This search has no saved locations — start a new search instead")
    set_status(search_id, state="running", progress="starting", started_at=utcnow().isoformat(),
               error=None, finished_at=None)
    background.add_task(run_area_search, search_id)
    return {"ok": True}


@router.delete("/searches/{search_id}")
def delete_search(search_id: int, session: Session = Depends(get_session)):
    search = session.get(AreaSearch, search_id)
    if not search:
        raise HTTPException(404)
    if search.source == "profile":
        raise HTTPException(400, "The profile-linked search can't be deleted — it follows the profile")
    for result in session.exec(
        select(AreaResult).where(AreaResult.area_search_id == search_id)
    ).all():
        session.delete(result)
    status_row = session.get(Meta, f"research_status:search:{search_id}")
    if status_row:
        session.delete(status_row)
    session.delete(search)
    session.commit()
    return {"ok": True}


@router.get("/status")
def research_status(search_id: int):
    from ..research.engine import get_status

    return get_status(search_id)


@router.get("")
def list_areas(search_id: int, session: Session = Depends(get_session)):
    search = session.get(AreaSearch, search_id)
    if not search:
        raise HTTPException(404)
    areas = session.exec(
        select(AreaResult).where(AreaResult.area_search_id == search_id)
    ).all()

    # Matching properties (passed filters, current criteria) per outcode
    counts: dict[str, int] = {}
    profile = session.get(SearchProfile, search.profile_id)
    if profile:
        matches = session.exec(
            select(MatchScore).where(
                MatchScore.profile_id == profile.id,
                MatchScore.criteria_version == profile.criteria_version,
                MatchScore.passed_filters == True,  # noqa: E712
            )
        ).all()
        for m in matches:
            prop = session.get(Property, m.property_id)
            if prop and prop.outcode:
                counts[prop.outcode] = counts.get(prop.outcode, 0) + 1

    out = []
    for a in sorted(areas, key=lambda a: -(a.scores.get("total", 0))):
        d = a.model_dump()
        d["match_count"] = counts.get(a.code, 0)
        out.append(d)
    return out
