from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import Listing, MatchScore, Property, SearchProfile, User

router = APIRouter(prefix="/api/properties", tags=["properties"])


def _card(prop: Property, listing: Listing | None, match: MatchScore | None, access: int | None = None) -> dict:
    return {
        "id": prop.id,
        "address": prop.address,
        "postcode": prop.postcode,
        "lat": prop.lat,
        "lng": prop.lng,
        "beds": prop.beds,
        "baths": prop.baths,
        "property_type": prop.property_type,
        "tenure": prop.tenure,
        "epc": prop.epc,
        "image": prop.image_urls[0] if prop.image_urls else None,
        "price": listing.price if listing else None,
        "price_qualifier": listing.price_qualifier if listing else None,
        "mode": listing.mode if listing else None,
        "status": listing.status if listing else None,
        "url": listing.url if listing else None,
        "portal": listing.portal if listing else None,
        "first_seen": listing.first_seen.isoformat() if listing else None,
        "price_history": listing.price_history if listing else [],
        "score": match.score if match else None,
        "passed_filters": match.passed_filters if match else None,
        "rationale": match.rationale if match else None,
        "access_score": access,
    }


@router.get("")
def list_properties(
    profile_id: int | None = None,
    sort: str = Query("score", pattern="^(score|newest|price_asc|price_desc|access)$"),
    min_score: float = 0,
    outcode: str | None = None,
    include_filtered: bool = False,
    limit: int = Query(60, le=200),
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Card feed. With profile_id, joins match scores for that profile's current criteria."""
    profile = session.get(SearchProfile, profile_id) if profile_id else None
    if profile and profile.user_id != user.id:
        raise HTTPException(404)

    props = session.exec(select(Property)).all()
    listings_by_prop: dict[int, Listing] = {}
    for listing in session.exec(select(Listing).where(Listing.status != "removed")).all():
        current = listings_by_prop.get(listing.property_id)
        if current is None or listing.last_seen > current.last_seen:
            listings_by_prop[listing.property_id] = listing

    matches: dict[int, MatchScore] = {}
    if profile:
        rows = session.exec(
            select(MatchScore).where(
                MatchScore.profile_id == profile.id,
                MatchScore.criteria_version == profile.criteria_version,
            )
        ).all()
        matches = {m.property_id: m for m in rows}

    from ..research.travel import access_scores

    visible: list[tuple[Property, Listing, MatchScore | None]] = []
    for prop in props:
        listing = listings_by_prop.get(prop.id)
        if listing is None:
            continue
        if outcode and prop.outcode != outcode.upper():
            continue
        match = matches.get(prop.id)
        if profile:
            if match is None:
                continue
            if not match.passed_filters and not include_filtered:
                continue
            if match.score < min_score:
                continue
            if profile.mode != listing.mode:
                continue
        visible.append((prop, listing, match))

    access = access_scores(session, [p.id for p, _, _ in visible], user.id)
    cards = [_card(p, l, m, access.get(p.id)) for p, l, m in visible]

    key = {
        "score": lambda c: -(c["score"] or 0),
        "newest": lambda c: c["first_seen"] or "",
        "price_asc": lambda c: c["price"] or 1 << 40,
        "price_desc": lambda c: -(c["price"] or 0),
        "access": lambda c: -(c["access_score"] or 0),
    }[sort]
    cards.sort(key=key, reverse=(sort == "newest"))
    return {"total": len(cards), "items": cards[offset : offset + limit]}


@router.get("/{property_id}")
def get_property(property_id: int, profile_id: int | None = None, session: Session = Depends(get_session), user: User = Depends(require_user)):
    prop = session.get(Property, property_id)
    if not prop:
        raise HTTPException(404)
    listings = session.exec(select(Listing).where(Listing.property_id == property_id)).all()
    match = None
    if profile_id:
        profile = session.get(SearchProfile, profile_id)
        if profile and profile.user_id == user.id:
            match = session.exec(
                select(MatchScore).where(
                    MatchScore.property_id == property_id,
                    MatchScore.profile_id == profile_id,
                    MatchScore.criteria_version == profile.criteria_version,
                )
            ).first()
    return {
        "property": prop,
        "listings": listings,
        "match": match,
    }


@router.get("/{property_id}/travel")
def property_travel(property_id: int, force: bool = False, session: Session = Depends(get_session), user: User = Depends(require_user)):
    """Travel times to this user's milestones (computes + caches ORS on first view)."""
    if not session.get(Property, property_id):
        raise HTTPException(404)
    from ..research.travel import access_score_single, compute_property_travel

    rows = compute_property_travel(property_id, user.id, force=force)
    score, avg_car = access_score_single(property_id, user.id)
    return {"milestones": rows, "access_score": score, "avg_car_minutes": avg_car}
