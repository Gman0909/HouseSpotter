from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, update as sa_update
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..history import CRITERIA_FIELDS, EDITABLE_FIELDS, apply_snapshot, snapshot_profile
from ..models import (
    AreaResult, AreaSearch, ChatMessage, MatchScore, Meta, Notification,
    ProfileSnapshot, ScrapeRun, SearchProfile,
)

router = APIRouter(prefix="/api/profiles", tags=["profiles"], dependencies=[Depends(require_user)])


@router.get("")
def list_profiles(session: Session = Depends(get_session)):
    return session.exec(select(SearchProfile)).all()


@router.post("")
def create_profile(body: dict, session: Session = Depends(get_session)):
    profile = SearchProfile(**{k: v for k, v in body.items() if k in EDITABLE_FIELDS})
    session.add(profile)
    session.commit()
    session.refresh(profile)
    snapshot_profile(session, profile, source="created")
    return profile


@router.get("/{profile_id}")
def get_profile(profile_id: int, session: Session = Depends(get_session)):
    profile = session.get(SearchProfile, profile_id)
    if not profile:
        raise HTTPException(404)
    return profile


def _geocode_locations(locations: list) -> list:
    """Fill in lat/lng for locations that only have a label (manual edits from the UI)."""
    from ..research.geo import geocode_place
    from ..research.sources import outcode_centroid
    from ..research.engine import OUTCODE_RE

    out = []
    for loc in locations:
        if loc.get("lat") is None or loc.get("lng") is None:
            label = (loc.get("label") or "").strip()
            coords = None
            if label:
                if OUTCODE_RE.match(label):
                    coords = outcode_centroid(label.upper())
                else:
                    coords = geocode_place(label)
            if not coords:
                raise HTTPException(422, f"Couldn't find location '{label}' — try a town name or postcode district")
            loc = {**loc, "lat": coords[0], "lng": coords[1]}
        loc.setdefault("radius_km", 8)
        out.append(loc)
    return out


@router.patch("/{profile_id}")
def update_profile(profile_id: int, body: dict, session: Session = Depends(get_session)):
    profile = session.get(SearchProfile, profile_id)
    if not profile:
        raise HTTPException(404)
    if isinstance(body.get("locations"), list):
        body["locations"] = _geocode_locations(body["locations"])
    criteria_changed = False
    for key, value in body.items():
        if key not in EDITABLE_FIELDS:
            continue
        if getattr(profile, key) != value:
            setattr(profile, key, value)
            if key in CRITERIA_FIELDS:
                criteria_changed = True
    if criteria_changed:
        profile.criteria_version += 1
    profile.updated_at = datetime.now(timezone.utc)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    if criteria_changed:
        from ..research.engine import ensure_profile_search
        from ..scoring.engine import rescore_profile_async

        snapshot_profile(session, profile, source="settings")
        rescore_profile_async(profile.id)
        ensure_profile_search(profile.id)  # keep the pinned area search in sync
    return profile


@router.get("/{profile_id}/history")
def profile_history(profile_id: int, session: Session = Depends(get_session)):
    if not session.get(SearchProfile, profile_id):
        raise HTTPException(404)
    return session.exec(
        select(ProfileSnapshot)
        .where(ProfileSnapshot.profile_id == profile_id)
        .order_by(ProfileSnapshot.id.desc())
    ).all()


@router.post("/{profile_id}/revert/{snapshot_id}")
def revert_profile(profile_id: int, snapshot_id: int, session: Session = Depends(get_session)):
    profile = session.get(SearchProfile, profile_id)
    snapshot = session.get(ProfileSnapshot, snapshot_id)
    if not profile or not snapshot or snapshot.profile_id != profile_id:
        raise HTTPException(404)
    profile = apply_snapshot(session, profile, snapshot)

    from ..research.engine import ensure_profile_search
    from ..scoring.engine import rescore_profile_async

    rescore_profile_async(profile.id)
    ensure_profile_search(profile.id)
    return profile


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, session: Session = Depends(get_session)):
    profile = session.get(SearchProfile, profile_id)
    if not profile:
        raise HTTPException(404)
    # Remove dependents first (SQLite enforces the FKs), keep audit rows by unlinking
    session.exec(sa_delete(MatchScore).where(MatchScore.profile_id == profile_id))
    session.exec(sa_delete(ProfileSnapshot).where(ProfileSnapshot.profile_id == profile_id))
    searches = session.exec(select(AreaSearch).where(AreaSearch.profile_id == profile_id)).all()
    for search in searches:
        session.exec(sa_delete(AreaResult).where(AreaResult.area_search_id == search.id))
        status_row = session.get(Meta, f"research_status:search:{search.id}")
        if status_row:
            session.delete(status_row)
        session.delete(search)
    session.exec(sa_delete(Notification).where(Notification.profile_id == profile_id))
    session.exec(sa_update(ScrapeRun).where(ScrapeRun.profile_id == profile_id).values(profile_id=None))
    session.exec(sa_update(ChatMessage).where(ChatMessage.profile_id == profile_id).values(profile_id=None))
    research_status = session.get(Meta, f"research_status:{profile_id}")
    if research_status:
        session.delete(research_status)
    session.delete(profile)
    session.commit()
    return {"ok": True}
