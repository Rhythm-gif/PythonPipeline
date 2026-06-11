"""
PACR Pipeline — Scheduler
APScheduler-based scheduler that runs the pipeline automatically.

Uses standard Cron expression for triggering and MongoDB-backed distributed 
locks to prevent overlapping runs across multiple server instances.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config.settings import get_settings
from app.common.logging import get_logger
from app.pipeline.locks import with_lock, init_locks_collection

logger = get_logger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
_last_run: Optional[datetime] = None
_is_running: bool = False


async def _execute_pipeline():
    global _last_run, _is_running
    _is_running = True
    _last_run = datetime.utcnow()
    try:
        from app.pipeline.service import run_pipeline
        summary = await run_pipeline()
        logger.info("Scheduled pipeline run complete", summary=summary)
    except Exception as exc:
        logger.error("Scheduled pipeline run failed", error=str(exc))
    finally:
        _is_running = False


async def _pipeline_job() -> None:
    """Wrapper that enforces distributed locking."""
    # lockAtMostFor: 1 hour (failsafe if process crashes)
    # lockAtLeastFor: 30 minutes (minimum time between runs)
    await with_lock(
        name="weekly-summary-pacr",
        lock_at_most_for_ms=4 * 60 * 60 * 1000,   # 4 hours max (failsafe)
        lock_at_least_for_ms=23 * 60 * 60 * 1000, # 23 hours min gap (once-a-day)
        func=_execute_pipeline,
    )


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()
    cron_expr = settings.cron_expression

    # Parse cron expression (minute, hour, day, month, day_of_week)
    parts = cron_expr.split()
    if len(parts) != 5:
        logger.error("Invalid CRON_EXPRESSION", expr=cron_expr)
        parts = ["0", "0", "*", "*", "*"] # Fallback: midnight daily

    minute, hour, day, month, day_of_week = parts

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _pipeline_job,
        trigger=CronTrigger(
            minute=minute, 
            hour=hour, 
            day=day, 
            month=month, 
            day_of_week=day_of_week
        ),
        id="pacr_pipeline",
        name="PACR Research Ingestion Pipeline",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    
    # Initialize locks collection (creates index) asynchronously in background
    asyncio.create_task(init_locks_collection())
    
    logger.info("Scheduler started with cron", cron=cron_expr)
    return _scheduler


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def get_scheduler_status() -> dict:
    if _scheduler is None:
        return {"running": False}
    jobs = _scheduler.get_jobs()
    return {
        "running": _scheduler.running,
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            }
            for j in jobs
        ],
        "last_pipeline_run": _last_run.isoformat() if _last_run else None,
        "pipeline_currently_running": _is_running,
    }


async def trigger_pipeline_now() -> dict:
    """Manually trigger the pipeline (for admin use only). Ignores lock to force run."""
    await _execute_pipeline()
    return {"triggered": True, "at": datetime.utcnow().isoformat()}
