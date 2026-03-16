"""APScheduler wrapper — cron-based scan and auto-convert jobs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

type AsyncJob = Callable[[], Coroutine[Any, Any, None]]

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_scheduler() -> None:
    s = get_scheduler()
    if not s.running:
        s.start()
        logger.info("Scheduler started")


def stop_scheduler() -> None:
    s = get_scheduler()
    if s.running:
        s.shutdown(wait=False)
        logger.info("Scheduler stopped")


def _replace_job(job_id: str, cron: str, fn: AsyncJob) -> None:
    s = get_scheduler()
    if s.get_job(job_id):
        s.remove_job(job_id)
    s.add_job(fn, CronTrigger.from_crontab(cron), id=job_id)
    logger.info("Scheduled job '%s' with cron '%s'", job_id, cron)


def update_scan_schedule(cron: str, fn: AsyncJob) -> None:
    _replace_job("scan", cron, fn)


def update_convert_schedule(cron: str, fn: AsyncJob) -> None:
    _replace_job("convert", cron, fn)


def remove_scan_schedule() -> None:
    s = get_scheduler()
    if s.get_job("scan"):
        s.remove_job("scan")


def remove_convert_schedule() -> None:
    s = get_scheduler()
    if s.get_job("convert"):
        s.remove_job("convert")
