from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import Milestone, TravelTime, User

router = APIRouter(prefix="/api/milestones", tags=["milestones"])

MAX_MILESTONES = 10


@router.get("")
def list_milestones(session: Session = Depends(get_session), user: User = Depends(require_user)):
    return session.exec(
        select(Milestone).where(Milestone.user_id == user.id).order_by(Milestone.id)
    ).all()


@router.post("")
def create_milestone(body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    label = (body.get("label") or "").strip()
    place = (body.get("place") or "").strip()
    if not label or not place:
        raise HTTPException(422, "label and place required")
    if len(session.exec(select(Milestone).where(Milestone.user_id == user.id)).all()) >= MAX_MILESTONES:
        raise HTTPException(422, f"Maximum {MAX_MILESTONES} milestones")

    from ..research.engine import OUTCODE_RE
    from ..research.geo import geocode_place
    from ..research.sources import outcode_centroid

    if OUTCODE_RE.match(place):
        coords = outcode_centroid(place.upper())
    else:
        coords = geocode_place(place)
    if not coords:
        raise HTTPException(422, f"Couldn't find '{place}' — try an address, place name or postcode")

    milestone = Milestone(
        user_id=user.id,
        label=label, place=place, lat=coords[0], lng=coords[1],
        weight=max(1, min(3, int(body.get("weight") or 2))),
    )
    session.add(milestone)
    session.commit()
    session.refresh(milestone)
    return milestone


@router.patch("/{milestone_id}")
def update_milestone(milestone_id: int, body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    milestone = session.get(Milestone, milestone_id)
    if not milestone or milestone.user_id != user.id:
        raise HTTPException(404)
    if "weight" in body:
        milestone.weight = max(1, min(3, int(body["weight"])))
    if body.get("label"):
        milestone.label = body["label"].strip()
    session.add(milestone)
    session.commit()
    session.refresh(milestone)
    return milestone


@router.delete("/{milestone_id}")
def delete_milestone(milestone_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    milestone = session.get(Milestone, milestone_id)
    if not milestone or milestone.user_id != user.id:
        raise HTTPException(404)
    from sqlalchemy import delete as sa_delete

    session.exec(sa_delete(TravelTime).where(TravelTime.milestone_id == milestone_id))
    session.delete(milestone)
    session.commit()
    return {"ok": True}
