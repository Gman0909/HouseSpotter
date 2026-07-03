"""Upsert normalized listings into the canonical Property/Listing store with de-duplication."""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..models import Listing, Property, utcnow
from .base import NormalizedListing

log = logging.getLogger("housespotter.normalizer")

# ~30m in degrees latitude; used for cross-portal geo matching
_GEO_EPS = 0.0003


def _norm_address(address: str) -> str:
    return re.sub(r"[^a-z0-9]", "", address.lower())


# Outcode ("CB4") optionally followed by the incode ("3NG"), at the end of the text —
# portal display addresses usually finish with one ("Mere Way, Cambridge, CB4")
_OUTCODE_RE = re.compile(r"\b([A-Za-z]{1,2}\d{1,2}[A-Za-z]?)(?:\s+\d[A-Za-z]{2})?\s*$")


def extract_outcode(text: str | None) -> str | None:
    if not text:
        return None
    m = _OUTCODE_RE.search(text.strip())
    return m.group(1).upper() if m else None


def _payload_hash(nl: NormalizedListing) -> str:
    payload = {
        "price": nl.price,
        "status": nl.status,
        "address": nl.address,
        "beds": nl.beds,
        "baths": nl.baths,
        "type": nl.property_type,
        "tenure": nl.tenure,
        "description": nl.description,
        "features": nl.features,
        "images": nl.image_urls,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _find_property(session: Session, nl: NormalizedListing) -> Property | None:
    key = _dedupe_key(nl)
    existing = session.exec(select(Property).where(Property.dedupe_key == key)).first()
    if existing:
        return existing
    # Cross-portal fuzzy match: same coords (±30m), beds and type
    if nl.lat is not None and nl.lng is not None and nl.beds is not None:
        candidates = session.exec(
            select(Property).where(
                Property.lat.between(nl.lat - _GEO_EPS, nl.lat + _GEO_EPS),  # type: ignore[union-attr]
                Property.lng.between(nl.lng - _GEO_EPS, nl.lng + _GEO_EPS),  # type: ignore[union-attr]
                Property.beds == nl.beds,
            )
        ).all()
        for cand in candidates:
            if cand.property_type == nl.property_type:
                return cand
    return None


def _dedupe_key(nl: NormalizedListing) -> str:
    addr = _norm_address(nl.address)
    if addr:
        return f"a:{addr}:{nl.beds or ''}"
    return f"g:{round(nl.lat or 0, 5)}:{round(nl.lng or 0, 5)}:{nl.beds or ''}"


def _richer(new_value, old_value):
    """Prefer non-empty new data but never blank out existing data."""
    if new_value in (None, "", []):
        return old_value
    return new_value


def upsert_listing(session: Session, nl: NormalizedListing) -> str:
    """Returns 'new' | 'updated' | 'unchanged'."""
    listing = session.exec(
        select(Listing).where(Listing.portal == nl.portal, Listing.portal_id == nl.portal_id)
    ).first()
    now = utcnow()
    new_hash = _payload_hash(nl)

    if listing:
        listing.last_seen = now
        if listing.status == "removed":
            listing.status = nl.status  # re-listed
        if listing.payload_hash == new_hash:
            session.add(listing)
            return "unchanged"
        if nl.price is not None and nl.price != listing.price:
            listing.price_history = listing.price_history + [
                {"date": now.date().isoformat(), "price": nl.price}
            ]
            listing.price = nl.price
        listing.price_qualifier = nl.price_qualifier or listing.price_qualifier
        listing.status = nl.status
        listing.payload_hash = new_hash
        prop = session.get(Property, listing.property_id)
        if prop:
            _update_property(prop, nl, now)
            session.add(prop)
        session.add(listing)
        return "updated"

    prop = _find_property(session, nl)
    if prop is None:
        prop = Property(dedupe_key=_dedupe_key(nl))
        _update_property(prop, nl, now)
        session.add(prop)
        session.flush()  # assign id
    else:
        _update_property(prop, nl, now)
        session.add(prop)

    listing = Listing(
        property_id=prop.id,
        portal=nl.portal,
        portal_id=nl.portal_id,
        url=nl.url,
        mode=nl.mode,
        price=nl.price,
        price_qualifier=nl.price_qualifier,
        status=nl.status,
        first_seen=now,
        last_seen=now,
        first_listed=nl.first_listed,
        price_history=(
            [{"date": now.date().isoformat(), "price": nl.price}] if nl.price is not None else []
        ),
        payload_hash=new_hash,
        raw=nl.raw,
    )
    session.add(listing)
    return "new"


def _update_property(prop: Property, nl: NormalizedListing, now: datetime) -> None:
    prop.address = _richer(nl.address, prop.address)
    prop.postcode = _richer(nl.postcode, prop.postcode)
    if prop.postcode and not prop.outcode:
        prop.outcode = prop.postcode.split()[0].upper()
    if not prop.outcode:
        prop.outcode = extract_outcode(prop.address)
    prop.lat = _richer(nl.lat, prop.lat)
    prop.lng = _richer(nl.lng, prop.lng)
    prop.beds = _richer(nl.beds, prop.beds)
    prop.baths = _richer(nl.baths, prop.baths)
    prop.property_type = _richer(nl.property_type, prop.property_type)
    prop.tenure = _richer(nl.tenure, prop.tenure)
    prop.floor_area_sqm = _richer(nl.floor_area_sqm, prop.floor_area_sqm)
    prop.epc = _richer(nl.epc, prop.epc)
    prop.features = _richer(nl.features, prop.features)
    if len(nl.description) > len(prop.description):
        prop.description = nl.description
    prop.image_urls = _richer(nl.image_urls, prop.image_urls)
    prop.floorplan_urls = _richer(nl.floorplan_urls, prop.floorplan_urls)
    prop.updated_at = now


def mark_missing_as_removed(
    session: Session, portal: str, mode: str, seen_portal_ids: set[str], grace_days: int = 3
) -> int:
    """Mark listings not seen for `grace_days` as removed (called after a full successful poll)."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    stale = session.exec(
        select(Listing).where(
            Listing.portal == portal,
            Listing.mode == mode,
            Listing.status != "removed",
        )
    ).all()
    count = 0
    for listing in stale:
        last_seen = listing.last_seen
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if listing.portal_id not in seen_portal_ids and last_seen < cutoff:
            listing.status = "removed"
            session.add(listing)
            count += 1
    return count
