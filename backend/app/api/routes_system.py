from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session, desc, select

from ..auth import require_user
from ..db import get_session
from ..models import ScrapeRun

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_user)])


@router.get("/scrape-runs")
def scrape_runs(limit: int = 50, session: Session = Depends(get_session)):
    return session.exec(select(ScrapeRun).order_by(desc(ScrapeRun.started_at)).limit(limit)).all()


# USD per million tokens (input, output); matched by model-id prefix
MODEL_PRICES = {
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@router.get("/usage")
def ai_usage(session: Session = Depends(get_session)):
    """This month's AI token spend vs the configured budget (Anthropic doesn't expose
    account balance via the API, so we track locally what this app spends)."""
    from datetime import date

    from ..config import settings
    from ..models import TokenUsage

    month = date.today().strftime("%Y-%m")
    rows = session.exec(select(TokenUsage).where(TokenUsage.month == month)).all()
    total_in = total_out = calls = 0
    cost = 0.0
    for row in rows:
        total_in += row.input_tokens
        total_out += row.output_tokens
        calls += row.calls
        price = next((p for prefix, p in MODEL_PRICES.items() if row.model.startswith(prefix)), (5.0, 25.0))
        cost += row.input_tokens / 1e6 * price[0] + row.output_tokens / 1e6 * price[1]
    budget = settings.ai_budget_usd or 0
    return {
        "month": month,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "calls": calls,
        "cost_usd": round(cost, 2),
        "budget_usd": budget,
        "remaining_usd": round(max(0.0, budget - cost), 2) if budget else None,
    }


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
