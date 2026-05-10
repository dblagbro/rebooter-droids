from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.commands import expire_overdue_commands
from app.services.schedule_runtime import tick as schedule_tick
from app.services.watchdog_runtime import tick as watchdog_tick

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _expire_job():
    n = expire_overdue_commands()
    if n:
        log.info("expired %d overdue command(s)", n)


def _watchdog_job():
    try:
        stats = watchdog_tick()
    except Exception:
        log.exception("watchdog tick crashed")
        return
    if stats.get("probed") or stats.get("fired") or stats.get("errors"):
        log.info("watchdog tick: %s", stats)


def _schedule_job():
    try:
        stats = schedule_tick()
    except Exception:
        log.exception("schedule tick crashed")
        return
    if stats.get("fired") or stats.get("errors"):
        log.info("schedule tick: %s", stats)


def start() -> None:
    """Start the in-process scheduler. Guarded so only one Gunicorn worker runs jobs."""
    global _scheduler
    if _scheduler is not None:
        return
    if os.environ.get("REBOOTER_SCHEDULER_DISABLED") == "1":
        return
    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    sched.add_job(_expire_job, "interval", seconds=30, id="expire_commands")
    sched.add_job(_watchdog_job, "interval", seconds=10, id="watchdog_tick")
    sched.add_job(_schedule_job, "interval", seconds=30, id="schedule_tick")
    sched.start()
    _scheduler = sched
    log.info(
        "APScheduler started: expire_commands every 30s, "
        "watchdog_tick every 10s, schedule_tick every 30s"
    )
