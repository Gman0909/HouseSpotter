"""Profile criteria history: snapshot on every criteria change, capped per profile.

This module owns the canonical field sets so the API routes and the intake agent
snapshot exactly what they edit.
"""
import logging

from sqlmodel import Session, select

from .models import ProfileSnapshot, SearchProfile

log = logging.getLogger("housespotter.history")

# Fields the client/agent may set — snapshots capture all of them.
EDITABLE_FIELDS = {
    "name", "mode", "active", "min_price", "max_price", "min_beds", "max_beds",
    "min_baths", "property_types", "tenures", "locations", "must_haves",
    "nice_to_haves", "exclusions", "excluded_keywords", "min_floor_area", "commutes", "qol_weights",
    "brief", "alert_threshold", "alert_min_access", "alert_channels", "alert_digest",
    "quiet_hours",
}
# Changes to these invalidate cached match scores (bump criteria_version).
CRITERIA_FIELDS = EDITABLE_FIELDS - {
    "name", "active", "alert_threshold", "alert_min_access", "alert_channels",
    "alert_digest", "quiet_hours",
}

SNAPSHOTS_KEPT = 50


def snapshot_profile(session: Session, profile: SearchProfile, source: str) -> None:
    """Record the profile's current state; prune history beyond SNAPSHOTS_KEPT."""
    data = {field: getattr(profile, field) for field in EDITABLE_FIELDS}
    session.add(ProfileSnapshot(
        profile_id=profile.id,
        criteria_version=profile.criteria_version,
        source=source,
        data=data,
    ))
    session.commit()

    snapshots = session.exec(
        select(ProfileSnapshot)
        .where(ProfileSnapshot.profile_id == profile.id)
        .order_by(ProfileSnapshot.id.desc())
    ).all()
    for old in snapshots[SNAPSHOTS_KEPT:]:
        session.delete(old)
    if len(snapshots) > SNAPSHOTS_KEPT:
        session.commit()


def apply_snapshot(session: Session, profile: SearchProfile, snapshot: ProfileSnapshot) -> SearchProfile:
    """Restore a snapshot's criteria onto the profile as a NEW criteria version."""
    from .models import utcnow

    for field in EDITABLE_FIELDS:
        if field in snapshot.data:
            setattr(profile, field, snapshot.data[field])
    profile.criteria_version += 1
    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    snapshot_profile(session, profile, source=f"revert to v{snapshot.criteria_version}")
    return profile
