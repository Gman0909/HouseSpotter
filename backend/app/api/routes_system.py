from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session, desc, select

from ..auth import require_user
from ..db import get_session
from ..models import ScrapeRun

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_user)])


@router.get("/scrape-runs")
def scrape_runs(limit: int = 50, session: Session = Depends(get_session)):
    return session.exec(select(ScrapeRun).order_by(desc(ScrapeRun.started_at)).limit(limit)).all()


@router.get("/scan-status")
def scan_status():
    from ..scheduler import scheduler
    from ..scraping.jobs import get_scan_status

    status = get_scan_status()
    next_run = None
    if scheduler.running:
        job = scheduler.get_job("poll")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    status["next_scheduled"] = next_run
    return status


@router.post("/poll-now")
def poll_now(background: BackgroundTasks):
    """Manually trigger a full poll cycle (also used to verify M1/M2)."""
    from ..scraping.jobs import get_scan_status, poll_all_profiles, set_scan_status
    from ..models import utcnow

    if get_scan_status().get("state") == "running":
        return {"ok": True, "detail": "scan already running"}
    set_scan_status(
        state="running", progress="starting", started_at=utcnow().isoformat(),
        finished_at=None, error=None,
    )
    background.add_task(poll_all_profiles)
    return {"ok": True, "detail": "poll started"}
