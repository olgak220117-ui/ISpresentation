import logging
import os
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .database import SessionLocal
from .sync import sync_all

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _run_sync():
    db = SessionLocal()
    try:
        results = sync_all(db)
        total = sum(sum(v.values()) for v in results.values())
        logger.info("Scheduled sync complete: %d properties, %d bookings upserted", len(results), total)
    except Exception:
        logger.exception("Scheduled sync failed")
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    interval = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_sync,
        trigger=IntervalTrigger(minutes=interval),
        id="ical_sync",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Sync scheduler started (every %d minutes)", interval)


def stop_scheduler():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
