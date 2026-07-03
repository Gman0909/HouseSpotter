"""APScheduler wiring. Jobs are registered in M2; M0 just provides lifecycle hooks."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger("housespotter.scheduler")

scheduler = BackgroundScheduler(timezone="Europe/London")


def start_scheduler() -> None:
    from .config import settings

    if not settings.scrape_enabled:
        log.info("Scraping disabled (HS_SCRAPE_ENABLED=false); scheduler not started")
        return
    from .scraping.jobs import register_jobs

    register_jobs(scheduler)
    scheduler.start()
    log.info("Scheduler started with %d job(s)", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
