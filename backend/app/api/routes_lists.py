from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import Listing, ListItem, Property, SavedList

router = APIRouter(prefix="/api/lists", tags=["lists"], dependencies=[Depends(require_user)])


@router.get("")
def list_lists(session: Session = Depends(get_session)):
    lists = session.exec(select(SavedList)).all()
    out = []
    for saved in lists:
        count = len(session.exec(select(ListItem).where(ListItem.list_id == saved.id)).all())
        out.append({"id": saved.id, "name": saved.name, "count": count})
    return out


@router.get("/saved-property-ids")
def saved_property_ids(session: Session = Depends(get_session)):
    """All property ids that are in at least one list (for badges on the feed)."""
    return sorted({item.property_id for item in session.exec(select(ListItem)).all()})


@router.get("/membership")
def membership(property_id: int, session: Session = Depends(get_session)):
    """Which lists a property is saved in."""
    items = session.exec(select(ListItem).where(ListItem.property_id == property_id)).all()
    out = []
    for item in items:
        saved = session.get(SavedList, item.list_id)
        if saved:
            out.append({"list_id": saved.id, "item_id": item.id, "name": saved.name})
    return out


@router.post("")
def create_list(body: dict, session: Session = Depends(get_session)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name required")
    saved = SavedList(name=name)
    session.add(saved)
    session.commit()
    session.refresh(saved)
    return saved


@router.delete("/{list_id}")
def delete_list(list_id: int, session: Session = Depends(get_session)):
    saved = session.get(SavedList, list_id)
    if not saved:
        raise HTTPException(404)
    for item in session.exec(select(ListItem).where(ListItem.list_id == list_id)).all():
        session.delete(item)
    session.delete(saved)
    session.commit()
    return {"ok": True}


@router.get("/{list_id}/items")
def list_items(list_id: int, session: Session = Depends(get_session)):
    items = session.exec(select(ListItem).where(ListItem.list_id == list_id)).all()
    out = []
    for item in items:
        prop = session.get(Property, item.property_id)
        listing = session.exec(
            select(Listing).where(Listing.property_id == item.property_id)
        ).first()
        out.append({
            "item": item,
            "address": prop.address if prop else "?",
            "image": (prop.image_urls[0] if prop and prop.image_urls else None),
            "price": listing.price if listing else None,
            "beds": prop.beds if prop else None,
            "property_id": item.property_id,
        })
    return out


@router.post("/{list_id}/items")
def add_item(list_id: int, body: dict, session: Session = Depends(get_session)):
    if not session.get(SavedList, list_id):
        raise HTTPException(404)
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
def update_item(list_id: int, item_id: int, body: dict, session: Session = Depends(get_session)):
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
def remove_item(list_id: int, item_id: int, session: Session = Depends(get_session)):
    item = session.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404)
    session.delete(item)
    session.commit()
    return {"ok": True}
