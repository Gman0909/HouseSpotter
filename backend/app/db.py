from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .models import Meta, User  # noqa: F401 — import registers all models

SCHEMA_VERSION = 12

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def _migrate_v2_area_searches(session: Session) -> None:
    """Wrap pre-existing areaprofile rows into a saved AreaSearch, then drop the old table."""
    import json

    from .models import AreaResult, AreaSearch

    exists = session.exec(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='areaprofile'")
    ).first()
    if not exists:
        return
    rows = session.exec(
        text(
            "SELECT profile_id, code, name, lat, lng, metrics, scores, narrative, "
            "listing_stats FROM areaprofile"
        )
    ).all()
    by_profile: dict[int, list] = {}
    for row in rows:
        by_profile.setdefault(row[0], []).append(row)
    for profile_id, profile_rows in by_profile.items():
        label = None
        status_row = session.get(Meta, f"research_status:{profile_id}")
        if status_row and status_row.value:
            try:
                label = json.loads(status_row.value).get("location")
            except json.JSONDecodeError:
                pass
        search = AreaSearch(
            profile_id=profile_id,
            name=label or "Earlier research",
            source="custom",
            locations=[],
        )
        session.add(search)
        session.flush()
        for row in profile_rows:
            session.add(AreaResult(
                area_search_id=search.id,
                code=row[1],
                name=row[2] or row[1],
                lat=row[3],
                lng=row[4],
                metrics=json.loads(row[5] or "{}"),
                scores=json.loads(row[6] or "{}"),
                narrative=row[7] or "",
                listing_stats=json.loads(row[8] or "{}"),
            ))
        search.last_run_at = search.created_at
        session.add(search)
    session.exec(text("DROP TABLE areaprofile"))
    session.exec(text("DELETE FROM meta WHERE key LIKE 'research_status:%'"))


# Numbered migrations applied in order after create_all. Entries are SQL strings or
# callables taking the session; create_all handles brand-new tables on fresh installs,
# these handle altering existing installs.
def _migrate_v3_baseline_snapshots(session: Session) -> None:
    """Give every existing profile an initial history snapshot.

    Raw SQL on purpose: migrations must not use ORM models, whose columns reflect
    the NEWEST schema (later migrations may not have run yet)."""
    import json

    from .history import EDITABLE_FIELDS
    from .models import ProfileSnapshot

    json_fields = {
        "property_types", "tenures", "locations", "must_haves", "nice_to_haves",
        "commutes", "qol_weights", "alert_channels", "quiet_hours",
    }
    rows = session.connection().execute(text("SELECT * FROM searchprofile")).mappings().all()
    for row in rows:
        data = {}
        for field in EDITABLE_FIELDS:
            value = row.get(field)
            if field in json_fields and isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            data[field] = value
        session.add(ProfileSnapshot(
            profile_id=row["id"],
            criteria_version=row["criteria_version"],
            source="baseline",
            data=data,
        ))
    session.commit()


def _migrate_v4_multi_user(session: Session) -> None:
    """Add ownership columns and assign all existing data to the first user (admin)."""
    from sqlalchemy.exc import OperationalError
    from sqlmodel import select

    for stmt in (
        'ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN DEFAULT 0',
        'ALTER TABLE "user" ADD COLUMN telegram_chat_id VARCHAR DEFAULT \'\'',
        'ALTER TABLE "user" ADD COLUMN email_to VARCHAR DEFAULT \'\'',
        "ALTER TABLE searchprofile ADD COLUMN user_id INTEGER",
        "ALTER TABLE savedlist ADD COLUMN user_id INTEGER",
        "ALTER TABLE milestone ADD COLUMN user_id INTEGER",
        "ALTER TABLE chatmessage ADD COLUMN user_id INTEGER",
    ):
        try:
            session.exec(text(stmt))
        except OperationalError:
            pass  # column already exists (fresh install via create_all)
    session.commit()

    first = session.exec(select(User).order_by(User.id)).first()
    if not first:
        return
    first.is_admin = True
    # Existing global alert targets become the admin's personal targets
    from .config import settings

    if not first.telegram_chat_id and settings.telegram_chat_id:
        first.telegram_chat_id = settings.telegram_chat_id
    if not first.email_to and settings.smtp_to:
        first.email_to = settings.smtp_to
    session.add(first)
    for table in ("searchprofile", "savedlist", "milestone", "chatmessage"):
        session.exec(text(f"UPDATE {table} SET user_id = {first.id} WHERE user_id IS NULL"))
    session.commit()


def _migrate_v5_portal_filters(session: Session) -> None:
    """Add exclusions + min floor area to search profiles."""
    from sqlalchemy.exc import OperationalError

    for stmt in (
        "ALTER TABLE searchprofile ADD COLUMN exclusions TEXT DEFAULT '[]'",
        "ALTER TABLE searchprofile ADD COLUMN min_floor_area INTEGER",
    ):
        try:
            session.exec(text(stmt))
        except OperationalError:
            pass  # column already exists (fresh install via create_all)
    session.commit()


def _migrate_v6_alert_min_access(session: Session) -> None:
    from sqlalchemy.exc import OperationalError

    try:
        session.exec(text("ALTER TABLE searchprofile ADD COLUMN alert_min_access INTEGER DEFAULT 0"))
    except OperationalError:
        pass  # column already exists (fresh install via create_all)
    session.commit()


def _migrate_v7_excluded_keywords(session: Session) -> None:
    """Add free-text excluded keywords/areas to search profiles."""
    from sqlalchemy.exc import OperationalError

    try:
        session.exec(text("ALTER TABLE searchprofile ADD COLUMN excluded_keywords TEXT DEFAULT '[]'"))
    except OperationalError:
        pass  # column already exists (fresh install via create_all)
    session.commit()


def _migrate_v8_fix_rent_prices(session: Session) -> None:
    """Drop rent prices corrupted by the OnTheMarket "£X pcm (£Y pw)" parse bug,
    which concatenated both amounts' digits and then multiplied by 52/12. Corrupt
    values are always in the millions; anything ≥ £100k pcm is garbage. The next
    poll re-fills the correct price (and appends it to the cleaned history)."""
    import json

    rows = session.connection().execute(
        text("SELECT id, price, price_history FROM listing WHERE mode = 'rent'")
    ).mappings().all()
    for row in rows:
        try:
            history = json.loads(row["price_history"] or "[]")
        except json.JSONDecodeError:
            history = []
        sane = [h for h in history if (h.get("price") or 0) < 100_000]
        price = row["price"]
        if price is not None and price >= 100_000:
            price = sane[-1]["price"] if sane else None
        if price != row["price"] or len(sane) != len(history):
            session.connection().execute(
                text("UPDATE listing SET price = :p, price_history = :h WHERE id = :id"),
                {"p": price, "h": json.dumps(sane), "id": row["id"]},
            )
    session.commit()


def _migrate_v9_listing_media(session: Session) -> None:
    """Per-listing photo galleries + floorplans, with detail-page enrichment tracking.
    Existing listings inherit their property's photo set, and every payload hash is
    cleared so the next poll re-stores each listing's own search photos."""
    from sqlalchemy.exc import OperationalError

    for stmt in (
        "ALTER TABLE listing ADD COLUMN image_urls TEXT DEFAULT '[]'",
        "ALTER TABLE listing ADD COLUMN gallery_urls TEXT DEFAULT '[]'",
        "ALTER TABLE listing ADD COLUMN floorplan_urls TEXT DEFAULT '[]'",
        "ALTER TABLE listing ADD COLUMN photos_enriched_at TIMESTAMP",
    ):
        try:
            session.exec(text(stmt))
        except OperationalError:
            pass  # column already exists (fresh install via create_all)
    session.commit()
    session.exec(text(
        "UPDATE listing SET image_urls = COALESCE("
        "(SELECT p.image_urls FROM property p WHERE p.id = listing.property_id), '[]')"
        " WHERE image_urls IS NULL OR image_urls = '[]'"
    ))
    session.exec(text("UPDATE listing SET payload_hash = ''"))
    session.commit()


def _migrate_v10_blacklisted_outcodes(session: Session) -> None:
    """Add the per-user global outcode blacklist."""
    from sqlalchemy.exc import OperationalError

    try:
        session.exec(text('ALTER TABLE "user" ADD COLUMN blacklisted_outcodes TEXT DEFAULT \'[]\''))
    except OperationalError:
        pass  # column already exists (fresh install via create_all)
    session.commit()


def _migrate_v11_furnish_type(session: Session) -> None:
    """Property.furnish_type for rentals, plus a re-enrich pass to backfill it:
    clearing photos_enriched_at on live rent listings makes the enrichment batch
    revisit their detail pages (galleries are kept — enrich only adds data)."""
    from sqlalchemy.exc import OperationalError

    try:
        session.exec(text("ALTER TABLE property ADD COLUMN furnish_type VARCHAR"))
    except OperationalError:
        pass  # column already exists (fresh install via create_all)
    session.exec(text(
        "UPDATE listing SET photos_enriched_at = NULL "
        "WHERE mode = 'rent' AND status != 'removed' "
        "AND portal IN ('rightmove', 'onthemarket')"
    ))
    session.commit()


def _migrate_v12_strict_furnish_rescore(session: Session) -> None:
    """The furnish check became strict (unknown now fails a must-have gate);
    invalidate cached scores for profiles that gate on it. The boot-time
    catch-up rescore refills them immediately."""
    session.exec(text(
        "UPDATE searchprofile SET criteria_version = criteria_version + 1 "
        "WHERE must_haves LIKE '%furnished%'"
    ))
    session.commit()


MIGRATIONS: dict[int, list] = {
    2: [_migrate_v2_area_searches],
    3: [_migrate_v3_baseline_snapshots],
    4: [_migrate_v4_multi_user],
    5: [_migrate_v5_portal_filters],
    6: [_migrate_v6_alert_min_access],
    7: [_migrate_v7_excluded_keywords],
    8: [_migrate_v8_fix_rent_prices],
    9: [_migrate_v9_listing_media],
    10: [_migrate_v10_blacklisted_outcodes],
    11: [_migrate_v11_furnish_type],
    12: [_migrate_v12_strict_furnish_rescore],
}


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = session.get(Meta, "schema_version")
        current = int(row.value) if row else 0
        for version in sorted(v for v in MIGRATIONS if v > current):
            for step in MIGRATIONS[version]:
                if callable(step):
                    step(session)
                else:
                    session.exec(text(step))
            current = version
        if row is None:
            row = Meta(key="schema_version", value=str(SCHEMA_VERSION))
            session.add(row)
        else:
            row.value = str(max(current, SCHEMA_VERSION))
        session.commit()
    _ensure_admin_user()


def _ensure_admin_user() -> None:
    from .auth import hash_password

    with Session(engine) as session:
        existing = session.exec(select(User)).first()
        if existing:
            return
        if not settings.password:
            raise RuntimeError(
                "No user exists and HS_PASSWORD is not set. "
                "Set HS_USERNAME / HS_PASSWORD in .env for first run."
            )
        session.add(User(
            username=settings.username,
            password_hash=hash_password(settings.password),
            is_admin=True,  # first user administers the server
        ))
        session.commit()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """For background jobs outside request context."""
    with Session(engine) as session:
        yield session
