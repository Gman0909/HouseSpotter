"""Poll orchestration: active profiles × portals × locations → normalize → store → score → notify."""
import logging
import threading

from sqlmodel import select

from ..db import session_scope
from ..models import ScrapeRun, SearchProfile, utcnow
from .base import PortalBlockedError
from .normalizer import mark_missing_as_removed, upsert_listing

log = logging.getLogger("housespotter.scraping")

# Only one poll cycle at a time (scheduler + manual "Scan now" can overlap)
_poll_lock = threading.Lock()

# Portal pause flags set on block detection; cleared after PAUSE_HOURS
_paused_until: dict[str, float] = {}
PAUSE_HOURS = 6


def get_adapters() -> list:
    from ..config import settings
    from .onthemarket import OnTheMarketAdapter
    from .rightmove import RightmoveAdapter

    adapters = [RightmoveAdapter(), OnTheMarketAdapter()]
    if settings.playwright_fallback:
        from .zoopla import ZooplaAdapter

        adapters.append(ZooplaAdapter())
    return adapters


def _portal_paused(portal: str) -> bool:
    import time

    until = _paused_until.get(portal, 0)
    return time.monotonic() < until


def _pause_portal(portal: str) -> None:
    import time

    _paused_until[portal] = time.monotonic() + PAUSE_HOURS * 3600
    log.warning("Portal %s paused for %dh after block detection", portal, PAUSE_HOURS)


def set_scan_status(**fields) -> None:
    import json

    from sqlmodel import Session

    from ..db import engine
    from ..models import Meta

    status = get_scan_status()
    status.update(fields)
    with Session(engine) as session:
        session.merge(Meta(key="scan_status", value=json.dumps(status)))
        session.commit()


def get_scan_status() -> dict:
    import json

    from sqlmodel import Session

    from ..db import engine
    from ..models import Meta

    with Session(engine) as session:
        row = session.get(Meta, "scan_status")
    if row and row.value:
        try:
            status = json.loads(row.value)
        except json.JSONDecodeError:
            return {"state": "idle"}
        if status.get("state") == "running" and status.get("started_at"):
            from datetime import datetime, timedelta, timezone

            try:
                started = datetime.fromisoformat(status["started_at"])
                if datetime.now(timezone.utc) - started > timedelta(minutes=45):
                    status["state"] = "error"
                    status["error"] = "Scan was interrupted"
            except ValueError:
                pass
        return status
    return {"state": "idle"}


def poll_all_profiles() -> None:
    if not _poll_lock.acquire(blocking=False):
        log.info("Poll already running; skipping")
        return
    set_scan_status(
        state="running", progress="starting", started_at=utcnow().isoformat(),
        finished_at=None, error=None,
    )
    try:
        _poll_all()
        set_scan_status(state="done", progress=None, finished_at=utcnow().isoformat())
    except Exception as exc:
        log.exception("Poll cycle failed")
        set_scan_status(state="error", error=str(exc), finished_at=utcnow().isoformat())
    finally:
        _poll_lock.release()


def _poll_all() -> None:
    with session_scope() as session:
        profiles = session.exec(
            select(SearchProfile).where(SearchProfile.active == True)  # noqa: E712
        ).all()
        profile_ids = [p.id for p in profiles]

    if not profile_ids:
        log.info("No active profiles; nothing to poll")
        return

    scored_profiles: set[int] = set()
    adapters = [a for a in get_adapters() if not _portal_paused(a.portal)]
    total_steps = len(adapters) * len(profile_ids)
    step = failures = 0
    for adapter in adapters:
        for profile_id in profile_ids:
            step += 1
            set_scan_status(state="running", progress=f"{adapter.portal} ({step}/{total_steps})")
            new_count = _poll_profile_portal(adapter, profile_id)
            if new_count >= 0:
                scored_profiles.add(profile_id)
            else:
                failures += 1
    set_scan_status(failures=failures, steps=total_steps)
    if total_steps and failures == total_steps:
        raise RuntimeError("every portal scan failed — check the Status page for details")

    # Re-score affected profiles, then send alerts for fresh matches
    from ..scoring.engine import score_profile

    set_scan_status(state="running", progress="scoring matches")
    for profile_id in scored_profiles:
        try:
            score_profile(profile_id)
        except Exception:
            log.exception("Scoring failed for profile %s", profile_id)

    from ..notify.alerts import send_alerts_for_new_matches

    try:
        send_alerts_for_new_matches()
    except Exception:
        log.exception("Alerting failed")


def _poll_profile_portal(adapter, profile_id: int) -> int:
    """Runs one portal for one profile. Returns number of new listings, or -1 on failure."""
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile:
            return -1
        run = ScrapeRun(portal=adapter.portal, profile_id=profile_id)
        session.add(run)
        session.commit()
        run_id = run.id
        locations = profile.locations or []
        mode = profile.mode

    found = new = updated = 0
    seen_ids: set[str] = set()
    error = ""
    blocked = False
    try:
        for location in locations:
            listings = adapter.search(_ProfileView(profile_id), location)
            found += len(listings)
            with session_scope() as session:
                for nl in listings:
                    seen_ids.add(nl.portal_id)
                    result = upsert_listing(session, nl)
                    if result == "new":
                        new += 1
                    elif result == "updated":
                        updated += 1
                session.commit()
        if locations:
            with session_scope() as session:
                removed = mark_missing_as_removed(session, adapter.portal, mode, seen_ids)
                session.commit()
                if removed:
                    log.info("%s: marked %d listings removed", adapter.portal, removed)
    except PortalBlockedError as exc:
        blocked = True
        error = str(exc)
        _pause_portal(adapter.portal)
        _notify_block(adapter.portal, error)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.exception("Poll failed: portal=%s profile=%s", adapter.portal, profile_id)

    with session_scope() as session:
        run = session.get(ScrapeRun, run_id)
        if run:
            run.finished_at = utcnow()
            run.found, run.new, run.updated = found, new, updated
            run.blocked = blocked
            run.error = error
            session.add(run)
            session.commit()
    return -1 if (blocked or error) else new


class _ProfileView:
    """Fresh read of a profile so adapters see current criteria without holding a session."""

    def __init__(self, profile_id: int):
        with session_scope() as session:
            profile = session.get(SearchProfile, profile_id)
            for field in (
                "id", "mode", "min_price", "max_price", "min_beds", "max_beds",
                "min_baths", "property_types", "tenures", "locations",
            ):
                setattr(self, field, getattr(profile, field))


def _notify_block(portal: str, detail: str) -> None:
    try:
        from sqlmodel import Session, select

        from ..db import engine
        from ..models import User
        from ..notify.channels import send_telegram

        # System notice → every admin with a chat ID
        with Session(engine) as s:
            chat_ids = [
                u.telegram_chat_id
                for u in s.exec(select(User).where(User.is_admin == True)).all()  # noqa: E712
                if u.telegram_chat_id
            ]
        for chat_id in chat_ids:
            send_telegram(
                f"⚠️ HouseSpotter: {portal} appears to be blocking requests; paused {PAUSE_HOURS}h.\n{detail}",
                chat_id=chat_id,
            )
    except Exception:
        log.debug("Could not send block notification", exc_info=True)


def refresh_all_research() -> None:
    """Weekly: keep each active profile's pinned area search fresh."""
    from ..research.engine import ensure_profile_search, run_area_search

    with session_scope() as session:
        profile_ids = [
            p.id for p in session.exec(
                select(SearchProfile).where(SearchProfile.active == True)  # noqa: E712
            ).all()
        ]
    for profile_id in profile_ids:
        try:
            search = ensure_profile_search(profile_id)
            if search and search.locations:
                run_area_search(search.id)
        except Exception:
            log.exception("Research refresh failed for profile %s", profile_id)


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        poll_all_profiles,
        IntervalTrigger(minutes=30, jitter=600),
        id="poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        refresh_all_research,
        CronTrigger(day_of_week="sun", hour=3, minute=30, jitter=1200),
        id="research",
        max_instances=1,
        coalesce=True,
    )

    def travel_batch():
        from ..research.travel import refresh_travel_batch

        refresh_travel_batch()

    scheduler.add_job(
        travel_batch,
        CronTrigger(hour=4, minute=15, jitter=600),
        id="travel",
        max_instances=1,
        coalesce=True,
    )
    log.info("Registered jobs: poll every 30min ± 10min; research weekly (Sun ~03:30); travel batch nightly (~04:15)")
