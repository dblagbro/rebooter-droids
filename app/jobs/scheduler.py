from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.commands import expire_overdue_commands

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _expire_job():
    n = expire_overdue_commands()
    if n:
        log.info("expired %d overdue command(s)", n)


def start() -> None:
    """Start the in-process scheduler. Guarded so only one Gunicorn worker runs jobs."""
    global _scheduler
    if _scheduler is not None:
        return
    if os.environ.get("REBOOTER_SCHEDULER_DISABLED") == "1":
        return
    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    sched.add_job(_expire_job, "interval", seconds=30, id="expire_commands")
    sched.start()
    _scheduler = sched
    log.info("APScheduler started: expire_commands every 30s")
