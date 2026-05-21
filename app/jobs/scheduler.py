from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.audit_prune import prune_old_audit_events
from app.services.commands import expire_overdue_commands
from app.services.device_power import compute_daily_rollups
from app.services.epg import refresh_epg
from app.services.external_sensors import poll_all_due as external_sensors_poll_all_due
from app.services.schedule_runtime import tick as schedule_tick
from app.services.sync_replicator import tick as sync_replicator_tick
from app.services.watchdog_runtime import tick as watchdog_tick

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


# org-boundary phase 2 (design §3.4): every scheduled job is a
# legitimately-unscoped "system context" — it operates across ALL orgs
# and must NOT be filtered by a single org's tenant scope. Each job body
# runs inside `tenant_scope.system()` — an explicit, audited bypass,
# never a bare unset ContextVar. A job that does genuine per-org work
# (iterating sites/devices) is responsible for re-entering
# `tenant_scope.org_context(org_id)` itself; the system() wrapper here is
# the safe default that says "this code path is deliberately cross-org."
def _system_job(fn):
    """Wrap a scheduler-job callable so its whole body runs under an
    explicit `tenant_scope.system()` bypass."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        from app.services import tenant_scope

        with tenant_scope.system():
            return fn(*args, **kwargs)

    return wrapper


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


def _audit_prune_job():
    """v0.5.36 (B1 RBAC P2): nightly soft-prune of old audit_events
    into audit_events_archive. Controlled by system.audit_retention_days
    runtime setting (default 90 days). Runs at 03:00 UTC so it happens
    after the day boundary and doesn't collide with power_rollups."""
    try:
        stats = prune_old_audit_events()
    except Exception:
        log.exception("audit prune job crashed")
        return
    if stats.get("archived") or stats.get("errors"):
        log.info("audit prune: %s", stats)


def _epg_refresh_job():
    """v0.5.64 (B17 Layer 2): refresh the TVMaze EPG cache (today +
    tomorrow's US schedule) and run the janitor. Every 6 h — cheap (two
    HTTP calls) and keeps the `epg_show_airing` probe's data fresh."""
    try:
        stats = refresh_epg()
    except Exception:
        log.exception("epg refresh job crashed")
        return
    if stats.get("stored") or stats.get("errors"):
        log.info("epg refresh: %s", stats)


def _webhook_delivery_job():
    """Tier-2 Feature 6 (notifications/webhooks): drain the
    `webhook_deliveries` queue. Picks up `pending`/`failed` rows whose
    `next_attempt_at` is due, sends each through the SSRF-guarded
    sender, and reschedules failures with exponential backoff. Runs
    every 15s — outbound webhook latency is not heartbeat-critical, and
    a 15s cadence keeps the per-tick HTTP fan-out modest."""
    try:
        from app.services.webhook_delivery import tick as webhook_delivery_tick

        stats = webhook_delivery_tick()
    except Exception:
        log.exception("webhook delivery tick crashed")
        return
    if stats.get("sent") or stats.get("failed") or stats.get("dead"):
        log.info("webhook delivery tick: %s", stats)


def _sync_replicator_job():
    """v0.5.48 (B11 Phase 5): multi-hub sync replicator. Polls peer
    hubs' /api/v1/sync/since endpoints every 3s, fetches outbox events,
    applies them locally, updates sync cursors. RFC-004 Option C
    target: ~1-3s steady-state latency."""
    try:
        stats = sync_replicator_tick()
    except Exception:
        log.exception("sync replicator tick crashed")
        return
    if stats.get("events_applied") or stats.get("errors"):
        log.info("sync replicator: %s", stats)


def start() -> None:
    """Start the in-process scheduler. Guarded so only one Gunicorn worker runs jobs."""
    global _scheduler
    if _scheduler is not None:
        return
    if os.environ.get("REBOOTER_SCHEDULER_DISABLED") == "1":
        return
    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    # org-boundary phase 2: every job body is wrapped in
    # `tenant_scope.system()` via `_system_job` — these jobs are
    # deliberately cross-org system contexts (design §3.4).
    sched.add_job(
        _system_job(_expire_job), "interval", seconds=30, id="expire_commands"
    )
    sched.add_job(
        _system_job(_watchdog_job), "interval", seconds=10, id="watchdog_tick"
    )
    sched.add_job(
        _system_job(_schedule_job), "interval", seconds=30, id="schedule_tick"
    )
    sched.add_job(
        _system_job(_external_sensors_job),
        "interval",
        seconds=30,
        id="external_sensors_tick",
    )
    # v0.5.48 (B11 Phase 5): multi-hub sync replicator every 3s for
    # ~1-3s steady-state latency per RFC-004 target.
    sched.add_job(
        _system_job(_sync_replicator_job),
        "interval",
        seconds=3,
        id="sync_replicator",
    )
    # Tier-2 Feature 6: outbound webhook delivery queue worker every 15s.
    sched.add_job(
        _system_job(_webhook_delivery_job),
        "interval",
        seconds=15,
        id="webhook_delivery",
    )
    # v0.5.29 (B16 Phase 1C): nightly daily rollups at 02:00 UTC.
    # Cron trigger so the run happens after the day boundary; aggregating
    # the prior full UTC day. Idempotent — re-running the same day
    # upserts cleanly (see compute_daily_rollups docstring).
    sched.add_job(
        _system_job(_power_rollups_job),
        "cron",
        hour=2, minute=0,
        id="power_rollups_daily",
    )
    # v0.5.36 (B1 RBAC P2): nightly audit prune at 03:00 UTC.
    # Soft-prunes audit_events older than system.audit_retention_days
    # into audit_events_archive. Date-rollover guard inside the job
    # prevents double-runs.
    sched.add_job(
        _system_job(_audit_prune_job),
        "cron",
        hour=3, minute=0,
        id="audit_prune_daily",
    )
    # v0.5.64 (B17 Layer 2): EPG cache refresh every 6 h. `next_run_time`
    # ~30 s out so the cache populates shortly after a deploy rather
    # than waiting a full 6 h interval.
    sched.add_job(
        _system_job(_epg_refresh_job),
        "interval",
        hours=6,
        id="epg_refresh",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    sched.start()
    _scheduler = sched

    # v0.5.63 (B17 Ship 3): start the MQTT subscriber here — this
    # `start()` is the single-worker bootstrap (the caller guards it),
    # so the long-lived paho threads inherit the one-worker guarantee.
    # Best-effort: a broker that's down must not block the scheduler.
    try:
        from app.services import mqtt_subscriber

        n_mqtt = mqtt_subscriber.start()
    except Exception:
        log.exception("MQTT subscriber failed to start")
        n_mqtt = 0

    log.info(
        "APScheduler started: expire_commands every 30s, "
        "watchdog_tick every 10s, schedule_tick every 30s, "
        "external_sensors_tick every 30s, "
        "sync_replicator every 3s, "
        "webhook_delivery every 15s, "
        "power_rollups_daily @ 02:00 UTC, "
        "audit_prune_daily @ 03:00 UTC, "
        "epg_refresh every 6h; "
        "MQTT subscribers: %d",
        n_mqtt,
    )
