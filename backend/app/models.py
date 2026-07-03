"""Canonical data model. JSON columns hold flexible sub-structures documented inline."""
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import JSON, Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class SearchProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    mode: str = "buy"  # buy | rent
    active: bool = True
    criteria_version: int = 1

    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_beds: Optional[int] = None
    max_beds: Optional[int] = None
    min_baths: Optional[int] = None
    # ["detached","semi-detached","terraced","flat","bungalow","land","park-home"]
    property_types: list = Field(default_factory=list, sa_column=Column(JSON))
    # ["freehold","leasehold","share-of-freehold"] (buy mode)
    tenures: list = Field(default_factory=list, sa_column=Column(JSON))
    # [{"label":"Guildford","lat":51.23,"lng":-0.57,"radius_km":8}]
    locations: list = Field(default_factory=list, sa_column=Column(JSON))
    # structured must-haves: {"parking":true,"garden":true,"chain_free":false,...}
    must_haves: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # [{"key":"parking","kind":"structured","weight":3},
    #  {"text":"period features","kind":"desire","weight":2}]
    nice_to_haves: list = Field(default_factory=list, sa_column=Column(JSON))
    # [{"label":"Office","lat":51.5,"lng":-0.1,"max_minutes":45,"mode":"transit"}]
    commutes: list = Field(default_factory=list, sa_column=Column(JSON))
    # {"transport":3,"safety":3,"amenities":2,"green":2,"schools":0,"quiet":1}
    qol_weights: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # free-text summary of what the user asked for (from intake chat)
    brief: str = Field(default="", sa_column=Column(Text))

    alert_threshold: int = 70
    alert_channels: list = Field(default_factory=lambda: ["telegram"], sa_column=Column(JSON))
    alert_digest: bool = False  # False = instant
    quiet_hours: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # {"start":"22:00","end":"08:00"}

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Property(SQLModel, table=True):
    """One real-world home, de-duplicated across portals."""
    id: Optional[int] = Field(default=None, primary_key=True)
    dedupe_key: str = Field(unique=True, index=True)
    address: str = ""
    postcode: Optional[str] = Field(default=None, index=True)
    outcode: Optional[str] = Field(default=None, index=True)  # e.g. "GU1"
    lat: Optional[float] = None
    lng: Optional[float] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    property_type: Optional[str] = None  # normalized taxonomy
    tenure: Optional[str] = None
    floor_area_sqm: Optional[float] = None
    epc: Optional[str] = None
    features: list = Field(default_factory=list, sa_column=Column(JSON))
    description: str = Field(default="", sa_column=Column(Text))
    image_urls: list = Field(default_factory=list, sa_column=Column(JSON))
    floorplan_urls: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Listing(SQLModel, table=True):
    """A Property as seen on one portal."""
    __table_args__ = (UniqueConstraint("portal", "portal_id", name="uq_portal_listing"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    property_id: int = Field(foreign_key="property.id", index=True)
    portal: str  # rightmove | zoopla | onthemarket
    portal_id: str
    url: str
    mode: str = "buy"  # buy | rent
    price: Optional[int] = None  # sale price or pcm rent, pounds
    price_qualifier: Optional[str] = None  # "Guide Price", "OIEO", "pcm", ...
    status: str = "live"  # live | under_offer | sold_stc | let_agreed | removed
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    first_listed: Optional[datetime] = None  # portal's own "added on" date
    # [{"date":"2026-07-01","price":450000}]
    price_history: list = Field(default_factory=list, sa_column=Column(JSON))
    payload_hash: str = ""  # hash of normalized payload, to skip unchanged listings
    raw: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class MatchScore(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("property_id", "profile_id", "criteria_version", name="uq_match"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    property_id: int = Field(foreign_key="property.id", index=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)
    criteria_version: int
    passed_filters: bool = True
    score: float = 0.0  # 0-100
    # [{"label":"Parking","kind":"structured","weight":3,"satisfaction":1.0,"reason":"..."}]
    breakdown: list = Field(default_factory=list, sa_column=Column(JSON))
    rationale: str = Field(default="", sa_column=Column(Text))
    computed_at: datetime = Field(default_factory=utcnow)


class AreaSearch(SQLModel, table=True):
    """A saved neighbourhood-research search. source='profile' searches mirror the
    profile's locations and are kept in sync; source='custom' are user-created."""
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)
    name: str
    source: str = "custom"  # profile | custom
    # [{"label":"Cambridge","lat":52.2,"lng":0.12,"radius_km":16}]
    locations: list = Field(default_factory=list, sa_column=Column(JSON))
    stale: bool = False  # profile location changed since last run
    created_at: datetime = Field(default_factory=utcnow)
    last_run_at: Optional[datetime] = None


class AreaResult(SQLModel, table=True):
    """One researched outcode belonging to a saved AreaSearch."""
    __table_args__ = (UniqueConstraint("area_search_id", "code", name="uq_area_result"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    area_search_id: int = Field(foreign_key="areasearch.id", index=True)
    code: str  # outcode, e.g. "CB2"
    name: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))  # raw counts/values
    scores: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 0-1 sub-scores + total
    narrative: str = Field(default="", sa_column=Column(Text))
    listing_stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    refreshed_at: datetime = Field(default_factory=utcnow)


class SavedList(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class ListItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("list_id", "property_id", name="uq_list_item"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="savedlist.id", index=True)
    property_id: int = Field(foreign_key="property.id", index=True)
    note: str = Field(default="", sa_column=Column(Text))
    status: str = ""  # freeform: "viewing booked", "offer made", ...
    added_at: datetime = Field(default_factory=utcnow)


class Notification(SQLModel, table=True):
    """Dedupe ledger: one row per (property, profile, channel, kind) ever sent."""
    __table_args__ = (
        UniqueConstraint("property_id", "profile_id", "channel", "kind", name="uq_notif"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    property_id: int = Field(foreign_key="property.id", index=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)
    channel: str  # telegram | email
    kind: str = "new_match"  # new_match | price_drop
    sent_at: datetime = Field(default_factory=utcnow)


class ScrapeRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    portal: str
    profile_id: Optional[int] = Field(default=None, foreign_key="searchprofile.id")
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    found: int = 0
    new: int = 0
    updated: int = 0
    blocked: bool = False
    error: str = ""


class LlmCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cache_key: str = Field(unique=True, index=True)
    model: str = ""
    response: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    """Intake chat history (single ongoing conversation per profile; profile_id nullable
    until the chat has created one)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    profile_id: Optional[int] = Field(default=None, foreign_key="searchprofile.id")
    role: str  # user | assistant
    content: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)


class Milestone(SQLModel, table=True):
    """A favourite place ('Office', 'Mum's house') used for travel times + access scores."""
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str
    place: str = ""  # what the user typed, for reference
    lat: float
    lng: float
    weight: int = 2  # importance 1-3, feeds the access score
    created_at: datetime = Field(default_factory=utcnow)


class TravelTime(SQLModel, table=True):
    """Cached real routing result property→milestone for one mode (estimates are not stored)."""
    __table_args__ = (
        UniqueConstraint("property_id", "milestone_id", "mode", name="uq_travel"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    property_id: int = Field(foreign_key="property.id", index=True)
    milestone_id: int = Field(foreign_key="milestone.id", index=True)
    mode: str  # car | cycle | walk
    minutes: Optional[float] = None  # None = unroutable
    km: Optional[float] = None
    provider: str = "ors"
    computed_at: datetime = Field(default_factory=utcnow)


class ProfileSnapshot(SQLModel, table=True):
    """Point-in-time copy of a profile's criteria — one per criteria version, so any
    change (agent chat, Settings, revert) can be undone."""
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)
    criteria_version: int
    source: str = ""  # created | chat | settings | revert | baseline
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class Meta(SQLModel, table=True):
    """Key-value store for schema version and small app state."""
    key: str = Field(primary_key=True)
    value: str = ""
