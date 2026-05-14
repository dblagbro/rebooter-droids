from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.commands import expire_overdue_commands
from app.services.device_power import compute_daily_rollups
from app.services.external_sensors import poll_all_due as external_sensors_poll_all_due
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


def _external_sensors_job():
    """v0.5.17 (B17 Layer 1): poll registered external sensor sources
    (Roku ECP first; HA / MQTT / EPG later) and append samples for the
    `roku_app_active` watchdog probe to read."""
    try:
        stats = external_sensors_poll_all_due()
    except Exception:
        log.exception("external sensors tick crashed")
        return
    if stats.get("polled") or stats.get("errors"):
        log.info("external sensors tick: %s", stats)


def _power_rollups_job():
    """v0.5.29 (B16 Phase 1C): nightly aggregation of yesterday's
    `device_power_samples` into `device_power_rollups`. Cron schedule
    at 02:00 UTC so it runs after the day boundary but before any US
    operator wakes up to look at /app/power."""
    try:
        stats = compute_daily_rollups()
    except Exception:
        log.exception("power rollups job crashed")
        return
    if stats.get("rollups_written"):
        log.info("power rollups: %s", stats)


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
    sched.add_job(
        _external_sensors_job, "interval", seconds=30, id="external_sensors_tick"
    )
    # v0.5.29 (B16 Phase 1C): nightly daily rollups at 02:00 UTC.
    # Cron trigger so the run happens after the day boundary; aggregating
    # the prior full UTC day. Idempotent — re-running the same day
    # upserts cleanly (see compute_daily_rollups docstring).
    sched.add_job(
        _power_rollups_job,
        "cron",
        hour=2, minute=0,
        id="power_rollups_daily",
    )
    sched.start()
    _scheduler = sched
    log.info(
        "APScheduler started: expire_commands every 30s, "
        "watchdog_tick every 10s, schedule_tick every 30s, "
        "external_sensors_tick every 30s, "
        "power_rollups_daily @ 02:00 UTC"
    )
