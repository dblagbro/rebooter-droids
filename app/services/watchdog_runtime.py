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
import re
import shutil
import socket
import subprocess
import urllib.parse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import WatchdogProbeEvent, WatchdogRule
from app.models.watchdog import RULE_STATUS_ARMED, RULE_STATUS_FIRING

log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3

# v0.5.9: default outbound targets for `probe.kind=='internet'` when the
# rule does not pin its own `probe.targets` list. Mirror the device-side
# internet watchdog mental model: three independent root resolvers, ANY
# success = healthy, ALL fail = real internet outage. Keeps the rule
# from firing on a single resolver blip.
DEFAULT_INTERNET_TARGETS: tuple[dict, ...] = (
    {"host": "1.1.1.1", "port": 53},
    {"host": "8.8.8.8", "port": 53},
    {"host": "4.2.2.2", "port": 53},
)
MAX_INTERNET_TARGETS = 8


# ── public entry point ───────────────────────────────────────────────────

def tick() -> dict:
    """Top-of-tick dispatcher. Returns a tiny stats dict for the
    APScheduler job log line + tests."""
    if os.environ.get("REBOOTER_WATCHDOG_DISABLED") == "1":
        return {"disabled": True}

    # v0.4.7 (B7): portal-wide maintenance mode short-circuits all
    # probes. Operator toggles via `/app/maintenance` (super-admin
    # only). The pause is global and immediate.
    from app.services import runtime_flags
    if runtime_flags.is_maintenance_mode_active():
        return {"disabled": False, "maintenance_mode": True, "considered": 0}

    now = datetime.now(timezone.utc)
    stats = {"considered": 0, "probed": 0, "fired": 0, "errors": 0, "in_window": 0}

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

            # v0.4.7 (B7): per-rule maintenance windows. Skip the
            # probe entirely if `now` falls inside any window — and
            # log the skip as a `maintenance_skip` event so the
            # operator sees the rule was suppressed (not silently
            # dropped).
            if _in_maintenance_window(rule, now):
                _record_event(
                    session, rule, "maintenance_skip",
                    {"reason": "rule maintenance window"},
                    now,
                )
                rule.last_probed_at = now
                rule.last_outcome = "maintenance_skip"
                stats["in_window"] += 1
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


def _in_maintenance_window(rule: WatchdogRule, now: datetime) -> bool:
    """v0.4.7: each window is `{"start": ISO8601, "end": ISO8601}`.
    Returns True if `now` is between any window's start and end.

    Errors in window-shape (bad ISO, missing keys) are treated as
    "no window" — never block a probe due to malformed config."""
    windows = rule.maintenance_windows or []
    if not windows:
        return False
    for w in windows:
        try:
            start = datetime.fromisoformat(w["start"])
            end = datetime.fromisoformat(w["end"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if start <= now <= end:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


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
            return _probe_internet(probe)
        if kind == "ping":
            return _probe_ping(probe)
        if kind == "tcp":
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


_PING_RTT_RE = re.compile(r"time[=<]\s*([0-9.]+)\s*ms", re.IGNORECASE)


def _probe_ping(probe: dict) -> tuple[str, dict]:
    """v0.5.13 (B6.1): real ICMP ping via /usr/bin/ping subprocess.

    Reads `probe.host` (required) and `probe.timeout_seconds`
    (default = PROBE_TIMEOUT_SECONDS). Sends one ICMP echo, parses
    the rtt from stdout, and returns success/failure with a details
    payload carrying `rtt_ms` (success) or `reason` + stderr snippet
    (failure). Falls back to a TCP-80 connect if `ping` is unavailable
    in the runtime (slim images, BSD-only minimal containers) — the
    details payload notes the fallback so operators don't conflate
    "real ICMP success" with "TCP-80 success".
    """
    host = (probe.get("host") or "").strip()
    if not host:
        return "failure", {"reason": "missing host"}
    try:
        timeout = int(probe.get("timeout_seconds") or PROBE_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = PROBE_TIMEOUT_SECONDS
    if timeout < 1:
        timeout = 1

    ping_bin = shutil.which("ping")
    if not ping_bin:
        ok = _probe_tcp(host, 80)
        return ("success" if ok else "failure"), {
            "fallback": "tcp_80",
            "reason": "ping binary not available in container",
        }

    # `-c 1` = one packet, `-W <s>` = per-packet timeout in seconds
    # (iputils-ping semantics). Use `-q` to keep output minimal.
    # Wall-clock cap = timeout + 1s buffer for subprocess overhead.
    try:
        proc = subprocess.run(
            [ping_bin, "-c", "1", "-W", str(timeout), host],
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
    except subprocess.TimeoutExpired:
        return "failure", {"reason": "ping subprocess timeout"}
    except FileNotFoundError:
        ok = _probe_tcp(host, 80)
        return ("success" if ok else "failure"), {
            "fallback": "tcp_80",
            "reason": "ping disappeared mid-probe",
        }

    if proc.returncode == 0:
        rtt_ms: float | None = None
        m = _PING_RTT_RE.search(proc.stdout)
        if m:
            try:
                rtt_ms = float(m.group(1))
            except ValueError:
                rtt_ms = None
        details: dict = {"host": host}
        if rtt_ms is not None:
            details["rtt_ms"] = rtt_ms
        return "success", details

    # exit 1 = no reply within timeout; exit 2 = error (unknown host, etc.)
    stderr_tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
    return "failure", {
        "host": host,
        "reason": "no_reply" if proc.returncode == 1 else "ping_error",
        "exit_code": proc.returncode,
        "stderr_tail": stderr_tail[0][:200],
    }


def _probe_tcp(host: str, port: int) -> bool:
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _probe_internet(probe: dict) -> tuple[str, dict]:
    """v0.5.9: multi-target outbound connectivity probe.

    Walks `probe.targets` (or DEFAULT_INTERNET_TARGETS when empty) and
    short-circuits on the first success. The rule is healthy if ANY
    target succeeds, and only fires `failure` when ALL fail — so a
    single resolver blip can't trigger a false power-cycle. The details
    payload always reports `targets_succeeded` and `targets_failed`
    arrays so the operator can tell `full internet outage` from
    `one resolver issue` in the event log.
    """
    raw_targets = probe.get("targets") or []
    if not isinstance(raw_targets, list) or not raw_targets:
        targets = [dict(t) for t in DEFAULT_INTERNET_TARGETS]
        defaulted = True
    else:
        targets = raw_targets[:MAX_INTERNET_TARGETS]
        defaulted = False

    # Probe every target every tick (not short-circuit) so the event
    # log always shows the complete picture — operator needs to tell
    # "1.1.1.1 down, others healthy" from "full outage". Worst-case
    # latency is targets × PROBE_TIMEOUT_SECONDS; with default 3 × 3s
    # = 9s it still fits inside the 10s scheduler tick.
    succeeded: list[dict] = []
    failed: list[dict] = []
    for t in targets:
        if not isinstance(t, dict):
            failed.append({"host": str(t), "port": None, "error": "bad target shape"})
            continue
        try:
            host = str(t.get("host") or "").strip()
            port = int(t.get("port") or 0)
        except (TypeError, ValueError):
            failed.append({"host": str(t.get("host") or ""), "port": t.get("port"),
                           "error": "bad target shape"})
            continue
        if not host or port <= 0:
            failed.append({"host": host, "port": port, "error": "bad target shape"})
            continue
        if _probe_tcp(host, port):
            succeeded.append({"host": host, "port": port})
        else:
            failed.append({"host": host, "port": port, "error": "tcp_connect_failed"})

    details = {
        "targets_succeeded": succeeded,
        "targets_failed": failed,
        "targets_total": len(targets),
    }
    if defaulted:
        details["used_default_targets"] = True
    if succeeded:
        return "success", details
    return "failure", details


def _probe_http(url: str, *, max_redirects: int = 3) -> bool:
    """v0.4.17 (BUG-048): follow up to 3 redirects so HTTPS upgrades
    and "/" → "/app/" style redirects don't trip a false failure.
    The probe is "is the site reachable + responsive", and a 302
    that resolves to a 200 is a healthy site by every operator's
    definition.
    """
    if not url:
        return False
    seen: set[str] = set()
    for _ in range(max_redirects + 1):
        if not url or url in seen:
            return False
        seen.add(url)
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
                status = resp.status
                if 200 <= status < 300:
                    return True
                if 300 <= status < 400:
                    location = resp.getheader("Location")
                    if not location:
                        return False
                    # Resolve relative redirects against current url.
                    url = urllib.parse.urljoin(url, location)
                    continue
                return False
            finally:
                conn.close()
        except Exception:
            return False
    # Followed too many redirects → treat as failure (loop or bad CDN).
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
