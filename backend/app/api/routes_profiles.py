from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..history import CRITERIA_FIELDS, EDITABLE_FIELDS, apply_snapshot, snapshot_profile
from ..models import ProfileSnapshot, SearchProfile, User
from ..userdata import delete_profile_data

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def _own_profile(session: Session, profile_id: int, user: User) -> SearchProfile:
    profile = session.get(SearchProfile, profile_id)
    if not profile or profile.user_id != user.id:
        raise HTTPException(404)
    return profile


@router.get("")
def list_profiles(session: Session = Depends(get_session), user: User = Depends(require_user)):
    return session.exec(select(SearchProfile).where(SearchProfile.user_id == user.id)).all()


@router.post("")
def create_profile(body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    profile = SearchProfile(**{k: v for k, v in body.items() if k in EDITABLE_FIELDS})
    profile.user_id = user.id
    session.add(profile)
    session.commit()
    session.refresh(profile)
    snapshot_profile(session, profile, source="created")
    return profile


@router.get("/{profile_id}")
def get_profile(profile_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    return _own_profile(session, profile_id, user)


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
def update_profile(profile_id: int, body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    profile = _own_profile(session, profile_id, user)
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
def profile_history(profile_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    _own_profile(session, profile_id, user)
    return session.exec(
        select(ProfileSnapshot)
        .where(ProfileSnapshot.profile_id == profile_id)
        .order_by(ProfileSnapshot.id.desc())
    ).all()


@router.post("/{profile_id}/revert/{snapshot_id}")
def revert_profile(profile_id: int, snapshot_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    profile = _own_profile(session, profile_id, user)
    snapshot = session.get(ProfileSnapshot, snapshot_id)
    if not snapshot or snapshot.profile_id != profile_id:
        raise HTTPException(404)
    profile = apply_snapshot(session, profile, snapshot)

    from ..research.engine import ensure_profile_search
    from ..scoring.engine import rescore_profile_async

    rescore_profile_async(profile.id)
    ensure_profile_search(profile.id)
    return profile


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    profile = _own_profile(session, profile_id, user)
    delete_profile_data(session, profile_id)
    session.delete(profile)
    session.commit()
    return {"ok": True}
