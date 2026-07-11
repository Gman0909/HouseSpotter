from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import Listing, ListItem, Property, SavedList, User

router = APIRouter(prefix="/api/lists", tags=["lists"])


def _own_list(session: Session, list_id: int, user: User) -> SavedList:
    saved = session.get(SavedList, list_id)
    if not saved or saved.user_id != user.id:
        raise HTTPException(404)
    return saved


def _user_list_ids(session: Session, user: User) -> set[int]:
    return {s.id for s in session.exec(select(SavedList).where(SavedList.user_id == user.id)).all()}


@router.get("")
def list_lists(session: Session = Depends(get_session), user: User = Depends(require_user)):
    lists = session.exec(select(SavedList).where(SavedList.user_id == user.id)).all()
    out = []
    for saved in lists:
        count = len(session.exec(select(ListItem).where(ListItem.list_id == saved.id)).all())
        out.append({"id": saved.id, "name": saved.name, "count": count})
    return out


@router.get("/saved-property-ids")
def saved_property_ids(session: Session = Depends(get_session), user: User = Depends(require_user)):
    """Property ids in at least one of THIS user's lists (for badges on the feed)."""
    list_ids = _user_list_ids(session, user)
    return sorted({i.property_id for i in session.exec(select(ListItem)).all() if i.list_id in list_ids})


@router.get("/membership")
def membership(property_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    """Which of this user's lists a property is saved in."""
    items = session.exec(select(ListItem).where(ListItem.property_id == property_id)).all()
    out = []
    for item in items:
        saved = session.get(SavedList, item.list_id)
        if saved and saved.user_id == user.id:
            out.append({"list_id": saved.id, "item_id": item.id, "name": saved.name})
    return out


@router.post("")
def create_list(body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name required")
    saved = SavedList(name=name, user_id=user.id)
    session.add(saved)
    session.commit()
    session.refresh(saved)
    return saved


@router.delete("/{list_id}")
def delete_list(list_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    saved = _own_list(session, list_id, user)
    for item in session.exec(select(ListItem).where(ListItem.list_id == list_id)).all():
        session.delete(item)
    session.delete(saved)
    session.commit()
    return {"ok": True}


def _item_rows(session: Session, user: User, pairs: list[tuple[ListItem, int, str]]) -> list[dict]:
    """Rich rows for saved items: full card payload plus description snippet and the
    list-item metadata (note, status, saved date). Carries MORE detail than the feed."""
    from ..research.stations import station_walk_map
    from ..research.travel import access_scores
    from .routes_properties import _card

    property_ids = [item.property_id for item, _, _ in pairs]
    access = access_scores(session, property_ids, user.id)
    stations = station_walk_map(session, property_ids)

    out = []
    for item, list_id, list_name in pairs:
        prop = session.get(Property, item.property_id)
        if not prop:
            continue
        # freshest live listing; fall back to any so delisted properties still
        # render their last-known state
        listings = session.exec(
            select(Listing).where(Listing.property_id == item.property_id)
        ).all()
        live = [l for l in listings if l.status != "removed"]
        listing = max(live or listings, key=lambda l: l.last_seen, default=None)
        card = _card(prop, listing, None, access.get(prop.id), True, stations.get(prop.id))
        out.append({
            "item": {
                "id": item.id, "note": item.note, "status": item.status,
                "added_at": item.added_at.isoformat(),
            },
            "card": card,
            "description": (prop.description or "")[:260],
            "delisted": not live,
            "property_id": item.property_id,
            "list_id": list_id,
            "list_name": list_name,
        })
    return out


@router.get("/all/items")
def all_items(session: Session = Depends(get_session), user: User = Depends(require_user)):
    """Every saved property across all of the user's lists. A property saved to two
    lists appears once per list (each carries its own note/status)."""
    lists = session.exec(select(SavedList).where(SavedList.user_id == user.id)).all()
    names = {l.id: l.name for l in lists}
    pairs: list[tuple[ListItem, int, str]] = []
    for lst in lists:
        for item in session.exec(select(ListItem).where(ListItem.list_id == lst.id)).all():
            pairs.append((item, lst.id, names[lst.id]))
    return _item_rows(session, user, pairs)


@router.get("/{list_id}/items")
def list_items(list_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    saved = _own_list(session, list_id, user)
    items = session.exec(select(ListItem).where(ListItem.list_id == list_id)).all()
    return _item_rows(session, user, [(i, list_id, saved.name) for i in items])


@router.post("/{list_id}/items")
def add_item(list_id: int, body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    _own_list(session, list_id, user)
    property_id = body.get("property_id")
    if not session.get(Property, property_id):
        raise HTTPException(404, "property not found")
    existing = session.exec(
        select(ListItem).where(ListItem.list_id == list_id, ListItem.property_id == property_id)
    ).first()
    if existing:
        return existing
    item = ListItem(list_id=list_id, property_id=property_id, note=body.get("note", ""))
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.patch("/{list_id}/items/{item_id}")
def update_item(list_id: int, item_id: int, body: dict, session: Session = Depends(get_session), user: User = Depends(require_user)):
    _own_list(session, list_id, user)
    item = session.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404)
    if "note" in body:
        item.note = body["note"]
    if "status" in body:
        item.status = body["status"]
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{list_id}/items/{item_id}")
def remove_item(list_id: int, item_id: int, session: Session = Depends(get_session), user: User = Depends(require_user)):
    _own_list(session, list_id, user)
    item = session.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404)
    session.delete(item)
    session.commit()
    return {"ok": True}
