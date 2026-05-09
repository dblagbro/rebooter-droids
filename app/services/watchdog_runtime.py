"""Watchdog probe runtime — v0.4.2 (B6).

A single APScheduler job (`watchdog_probe_tick`) runs every 10 s.
Each tick:

1. Loads enabled rules whose `last_probed_at + window_seconds` has
   elapsed (or `last_probed_at IS NULL`).
2. For each due rule, dispatches the probe (one function per
   probe-kind), records a `WatchdogProbeEvent`, updates the
   rule's runtime counters.
3. On `failure_streak >= failure_threshold` (and outside cooldown),
   fires the rule's action (cycle / hold_off / notify_only) and
   records an `action_fired` event.
4. On `recovery_streak >= recovery_threshold`, resets the counters
   and records a `recovery` event.

Probes intentionally use only stdlib (socket / urllib / http.client)
so we don't pull a fresh dependency. Each probe is best-effort with
a tight timeout (3 s default).

Design notes:
- The runtime never raises out of the tick — any unhandled per-rule
  exception is recorded as `outcome = 'probe_error'` and the rule
  keeps marching.
- Cooldown: once an action fires, the rule won't fire again until
  `last_action_at + cooldown_seconds` has elapsed. During cooldown
  failures still get logged with `outcome = 'cooldown_skip'` so the
  operator sees what's happening.
- Action dispatch reuses `commands.enqueue_for_device` for cycle
  and hold_off — same path the operator's manual buttons use, so
  authz / audit / TTL behave identically.
- The runtime can be disabled with `REBOOTER_WATCHDOG_DISABLED=1`
  for emergency stop without a code change.
"""

from __future__ import annotations

import http.client
import logging
import os
import socket
import urllib.parse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import WatchdogProbeEvent, WatchdogRule
from app.models.watchdog import RULE_STATUS_ARMED, RULE_STATUS_FIRING

log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3


# ── public entry point ───────────────────────────────────────────────────

def tick() -> dict:
    """Top-of-tick dispatcher. Returns a tiny stats dict for the
    APScheduler job log line + tests."""
    if os.environ.get("REBOOTER_WATCHDOG_DISABLED") == "1":
        return {"disabled": True}

    now = datetime.now(timezone.utc)
    stats = {"considered": 0, "probed": 0, "fired": 0, "errors": 0}

    with session_scope() as session:
        rules = list(
            session.scalars(
                select(WatchdogRule).where(
                    WatchdogRule.enabled.is_(True),
                    WatchdogRule.status != "disabled",
                )
            )
        )
        for rule in rules:
            stats["considered"] += 1
            if not _rule_is_due(rule, now):
                continue
            try:
                outcome, details = _run_probe(rule)
                stats["probed"] += 1
            except Exception as e:
                outcome, details = "probe_error", {"error": str(e)}
                stats["errors"] += 1

            _record_event(session, rule, outcome, details, now)
            fired = _update_state_and_maybe_fire(session, rule, outcome, details, now)
            if fired:
                stats["fired"] += 1

        session.flush()

    return stats


# ── due-time check ───────────────────────────────────────────────────────

def _rule_is_due(rule: WatchdogRule, now: datetime) -> bool:
    if rule.last_probed_at is None:
        return True
    return (now - rule.last_probed_at) >= timedelta(seconds=rule.window_seconds)


# ── probe dispatch ───────────────────────────────────────────────────────

def _run_probe(rule: WatchdogRule) -> tuple[str, dict]:
    """Returns (outcome, details). outcome ∈ {'success', 'failure'}."""
    probe = rule.probe or {}
    kind = probe.get("kind")
    try:
        if kind == "internet":
            ok = _probe_tcp("1.1.1.1", 53)
        elif kind == "ping":
            # No raw ICMP from the container by default — fall back
            # to a TCP-port-80 probe to the host. If the operator
            # really wants ICMP they can set probe.kind='custom'
            # later, but for the common watchdog case ping ≈ "is
            # this host reachable on port 80".
            ok = _probe_tcp(probe.get("host", ""), int(probe.get("port", 80)))
        elif kind == "tcp":
            ok = _probe_tcp(probe.get("host", ""), int(probe.get("port", 0)))
        elif kind == "http":
            ok = _probe_http(probe.get("url", ""))
        elif kind == "dns":
            ok = _probe_dns(probe.get("hostname", ""))
        elif kind == "gateway":
            # No device-side gateway IP wiring in v0.4.2 yet — treat
            # as 'success' (skip) until we know the device's LAN
            # gateway. Gateway probes are queued for v0.4.3+ when the
            # device firmware reports its LAN gateway in heartbeat.
            return "success", {"skipped": "gateway probe needs device-side gateway info"}
        else:
            return "failure", {"reason": f"unknown probe kind: {kind}"}
    except Exception as e:
        return "failure", {"reason": "probe_exception", "error": str(e)}

    return ("success" if ok else "failure"), {}


def _probe_tcp(host: str, port: int) -> bool:
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _probe_http(url: str) -> bool:
    if not url:
        return False
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https"):
            return False
        host = u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        if u.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=PROBE_TIMEOUT_SECONDS)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=PROBE_TIMEOUT_SECONDS)
        try:
            conn.request("GET", path, headers={"User-Agent": "rebooter-watchdog/0.4.2"})
            resp = conn.getresponse()
            return 200 <= resp.status < 300
        finally:
            conn.close()
    except Exception:
        return False


def _probe_dns(hostname: str) -> bool:
    if not hostname:
        return False
    try:
        socket.setdefaulttimeout(PROBE_TIMEOUT_SECONDS)
        socket.gethostbyname(hostname)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


# ── event log + state update ─────────────────────────────────────────────

def _record_event(
    session, rule: WatchdogRule, outcome: str, details: dict, at: datetime
) -> None:
    session.add(
        WatchdogProbeEvent(
            rule_id=rule.id,
            at=at,
            outcome=outcome,
            details=details or {},
        )
    )


def _update_state_and_maybe_fire(
    session, rule: WatchdogRule, outcome: str, details: dict, now: datetime
) -> bool:
    rule.last_probed_at = now
    rule.last_outcome = outcome

    if outcome == "success":
        if rule.failure_streak > 0 or rule.status == RULE_STATUS_FIRING:
            rule.recovery_streak += 1
            if rule.recovery_streak >= rule.recovery_threshold:
                rule.failure_streak = 0
                rule.recovery_streak = 0
                rule.status = RULE_STATUS_ARMED
                _record_event(
                    session, rule, "recovery",
                    {"reason": "recovery_threshold reached"}, now,
                )
        else:
            rule.recovery_streak = 0
        return False

    if outcome != "failure":
        return False

    rule.recovery_streak = 0
    rule.failure_streak += 1

    if rule.failure_streak < rule.failure_threshold:
        return False

    # Cooldown gate.
    if rule.last_action_at is not None:
        if (now - rule.last_action_at) < timedelta(seconds=rule.cooldown_seconds):
            _record_event(
                session, rule, "cooldown_skip",
                {"failure_streak": rule.failure_streak}, now,
            )
            return False

    # Threshold crossed and not in cooldown — fire.
    fired_details = _fire_action(rule)
    rule.status = RULE_STATUS_FIRING
    rule.last_action_at = now
    _record_event(session, rule, "action_fired", fired_details, now)
    return True


# ── action dispatch ──────────────────────────────────────────────────────

def _fire_action(rule: WatchdogRule) -> dict:
    """Returns details dict to embed in the action_fired event.

    Exceptions are caught here so the tick keeps moving — the action
    error gets recorded as part of the event instead of bubbling.
    """
    action = rule.action or {}
    target = rule.target or {}
    kind = action.get("kind")
    try:
        if kind == "cycle":
            return _fire_cycle(rule, target, action)
        if kind == "hold_off":
            return _fire_hold_off(rule, target)
        if kind == "notify_only":
            return {"action": "notify_only", "note": "no power action"}
        return {"action": kind, "error": f"unsupported action: {kind}"}
    except Exception as e:
        log.exception("watchdog action failed for rule %s", rule.id)
        return {"action": kind, "error": str(e)}


def _fire_cycle(rule: WatchdogRule, target: dict, action: dict) -> dict:
    from app.services.commands import enqueue_for_device

    device_ids = _resolve_target_devices(target)
    if not device_ids:
        return {"action": "cycle", "skipped": "no devices in target"}

    payload = {
        "power_off_seconds": int(action.get("power_off_seconds", 5)),
        "post_reboot_holdoff_seconds": int(action.get("post_reboot_holdoff_seconds", 180)),
    }
    enqueued: list[str] = []
    skipped: list[dict] = []
    for did in device_ids:
        try:
            cmd = enqueue_for_device(
                device_id=did,
                cmd_type="relay_cycle",
                payload=payload,
                issued_by_user_id=None,
                # Watchdog rules treat is_protected as a soft gate —
                # if the operator has marked a device protected, the
                # rule should NOT power-cycle it. enqueue_for_device
                # will raise DeviceLockedError; we capture as 'skipped'.
                override_lockout=False,
            )
            enqueued.append(cmd.id)
        except Exception as e:
            skipped.append({"device_id": did, "error": str(e)})
    return {
        "action": "cycle",
        "rule_id": rule.id,
        "enqueued": enqueued,
        "skipped": skipped,
        "payload": payload,
    }


def _fire_hold_off(rule: WatchdogRule, target: dict) -> dict:
    from app.services.commands import enqueue_for_device

    device_ids = _resolve_target_devices(target)
    if not device_ids:
        return {"action": "hold_off", "skipped": "no devices in target"}

    held: list[str] = []
    skipped: list[dict] = []
    for did in device_ids:
        try:
            enqueue_for_device(
                device_id=did,
                cmd_type="relay_off",
                payload=None,
                issued_by_user_id=None,
                set_hold_off=True,
            )
            held.append(did)
        except Exception as e:
            skipped.append({"device_id": did, "error": str(e)})
    return {"action": "hold_off", "rule_id": rule.id, "held": held, "skipped": skipped}


def _resolve_target_devices(target: dict) -> list[str]:
    from app.models import Device, GroupMembership

    kind = target.get("kind")
    with session_scope() as session:
        if kind == "device":
            return [target["id"]] if target.get("id") else []
        if kind == "group":
            gid = target.get("id")
            if not gid:
                return []
            rows = list(
                session.scalars(
                    select(GroupMembership.device_id).where(
                        GroupMembership.group_id == gid
                    )
                )
            )
            return [r for r in rows if r]
        if kind == "tag":
            # Tag-as-target is shaped but no device-tag table ships
            # in v0.4.2; runtime treats tag targets as no-op until
            # the tag store lands. The rule still records an
            # action_fired event so the operator sees the rule
            # decided to fire — they just need to migrate the rule
            # to a device/group target meanwhile.
            return []
        return []
