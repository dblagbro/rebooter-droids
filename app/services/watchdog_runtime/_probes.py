"""Probe implementations — pure I/O, no DB writes.

Each `_probe_<kind>` returns either:
- `bool` (legacy shape used internally by `run_probe`), or
- `tuple[str, dict]` — `(outcome, details)` where outcome ∈
  {'success', 'failure'} and details is the event-log payload.

`run_probe(rule)` is the top-of-tick dispatcher. Probes use only the
stdlib (socket / urllib / http.client / subprocess) so this module
adds no new dependency footprint.
"""

from __future__ import annotations

import http.client
import re
import shutil
import socket
import subprocess
import urllib.parse

from app.models import WatchdogRule

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


def run_probe(rule: WatchdogRule) -> tuple[str, dict]:
    """Returns (outcome, details). outcome ∈ {'success', 'failure'}."""
    probe = rule.probe or {}
    kind = probe.get("kind")
    try:
        if kind == "internet":
            return _probe_internet(probe)
        if kind == "ping":
            return _probe_ping(probe)
        if kind == "roku_app_active":
            return _probe_roku_app_active(probe)
        if kind == "ha_state_is":
            return _probe_ha_state_is(probe)
        if kind == "weather_alert_active":
            return _probe_weather_alert_active(probe)
        if kind == "ical_event_active":
            return _probe_ical_event_active(probe)
        if kind == "tcp":
            ok = _probe_tcp(probe.get("host", ""), int(probe.get("port", 0)))
        elif kind == "http":
            ok = _probe_http(probe.get("url", ""))
        elif kind == "dns":
            ok = _probe_dns(probe.get("hostname", ""))
        elif kind == "gateway":
            # No device-side gateway IP wiring in v0.4.2 yet — treat as
            # 'success' (skip) until the device firmware reports its
            # LAN gateway in heartbeat.
            return "success", {"skipped": "gateway probe needs device-side gateway info"}
        else:
            return "failure", {"reason": f"unknown probe kind: {kind}"}
    except Exception as e:
        return "failure", {"reason": "probe_exception", "error": str(e)}

    return ("success" if ok else "failure"), {}


_PING_RTT_RE = re.compile(r"time[=<]\s*([0-9.]+)\s*ms", re.IGNORECASE)


def _probe_ping(probe: dict) -> tuple[str, dict]:
    """v0.5.13 (B6.1): real ICMP ping via /usr/bin/ping subprocess.

    Reads `probe.host` (required) and `probe.timeout_seconds` (default =
    PROBE_TIMEOUT_SECONDS). Sends one ICMP echo, parses the rtt from
    stdout, returns success/failure with a details payload carrying
    `rtt_ms` (success) or `reason` + stderr snippet (failure). Falls
    back to a TCP-80 connect if `ping` is unavailable in the runtime
    (slim images, BSD-only minimal containers) — the details payload
    notes the fallback so operators don't conflate "real ICMP success"
    with "TCP-80 success".
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
    # (iputils-ping semantics). Wall-clock cap = timeout + 1s buffer
    # for subprocess overhead.
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

    Walks `probe.targets` (or DEFAULT_INTERNET_TARGETS when empty). ANY
    success = healthy; ALL fail = failure. Probes every target every
    tick (not short-circuit) so the event log always shows the
    complete picture — operator can tell "1.1.1.1 down, others
    healthy" from "full outage". Worst-case latency:
    targets × PROBE_TIMEOUT_SECONDS = 3 × 3s = 9s (fits inside the 10s
    scheduler tick).
    """
    raw_targets = probe.get("targets") or []
    if not isinstance(raw_targets, list) or not raw_targets:
        targets = [dict(t) for t in DEFAULT_INTERNET_TARGETS]
        defaulted = True
    else:
        targets = raw_targets[:MAX_INTERNET_TARGETS]
        defaulted = False

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
    """v0.4.17 (BUG-048): follow up to 3 redirects so HTTPS upgrades and
    "/" → "/app/" style redirects don't trip a false failure."""
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
                    url = urllib.parse.urljoin(url, location)
                    continue
                return False
            finally:
                conn.close()
        except Exception:
            return False
    # Followed too many redirects → treat as failure.
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


def _probe_roku_app_active(probe: dict) -> tuple[str, dict]:
    """v0.5.17 (B17 Layer 1): rule fires when the named Roku is on the
    named app right now.

    Rule shape:
        probe = {"kind": "roku_app_active",
                 "source_id": "ext_…",
                 "app_name": "Spectrum TV",
                 "max_sample_age_seconds": 120}

    Reads the latest sample from `services.external_sensors`. Stale
    samples (older than `max_sample_age_seconds`, default 120 s)
    return `failure` with `reason='stale_sample'` so a dead poller
    can never make a rule "stick true" indefinitely.

    Matching is case-insensitive substring against `payload.active_app`
    so the operator can register "Spectrum TV" or "spectrum" or
    "Spectrum TV (HD)" depending on what their box reports.
    """
    source_id = (probe.get("source_id") or "").strip()
    expected = (probe.get("app_name") or "").strip()
    if not source_id:
        return "failure", {"reason": "missing source_id"}
    if not expected:
        return "failure", {"reason": "missing app_name"}
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 120)
    except (TypeError, ValueError):
        max_age = 120

    # Deferred import keeps watchdog_runtime self-contained when no
    # roku_app_active rules exist (the external_sensors service pulls
    # in models + db that we don't need otherwise).
    from app.services.external_sensors import latest_active_app

    sample = latest_active_app(source_id, max_age_seconds=max_age)
    if sample is None:
        return "failure", {
            "reason": "stale_sample",
            "source_id": source_id,
            "max_sample_age_seconds": max_age,
        }
    payload = sample.get("payload") or {}
    actual = (payload.get("active_app") or "").strip()
    if not actual:
        return "failure", {
            "reason": "no_active_app",
            "source_id": source_id,
            "sampled_at": sample.get("sampled_at"),
            "screensaver_active": payload.get("screensaver_active"),
        }
    match = expected.lower() in actual.lower()
    details = {
        "source_id": source_id,
        "expected_app": expected,
        "actual_app": actual,
        "sampled_at": sample.get("sampled_at"),
        "screensaver_active": payload.get("screensaver_active"),
    }
    return ("success" if match else "failure"), details


def _probe_ha_state_is(probe: dict) -> tuple[str, dict]:
    """v0.5.23 (B17): rule fires when an HA entity is in an expected state.

    Rule shape:
        probe = {"kind": "ha_state_is",
                 "source_id": "ext_…",
                 "entity_id": "sensor.living_room_motion",
                 "expected_state": "on",
                 "max_sample_age_seconds": 60}

    Matching is case-insensitive exact-equality against the entity's
    `state` field. Stale samples → failure (`reason='stale_sample'`).
    """
    source_id = (probe.get("source_id") or "").strip()
    entity_id = (probe.get("entity_id") or "").strip()
    expected = str(probe.get("expected_state") or "").strip()
    if not source_id or not entity_id or not expected:
        return "failure", {"reason": "missing source_id / entity_id / expected_state"}
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 60)
    except (TypeError, ValueError):
        max_age = 60

    from app.services.external_sensors import latest_sample

    sample = latest_sample(source_id, max_age_seconds=max_age)
    if sample is None:
        return "failure", {
            "reason": "stale_sample",
            "source_id": source_id,
            "max_sample_age_seconds": max_age,
        }
    payload = sample.get("payload") or {}
    entities = (payload.get("entities") or {}) if isinstance(payload, dict) else {}
    entry = entities.get(entity_id) if isinstance(entities, dict) else None
    if not isinstance(entry, dict):
        return "failure", {
            "reason": "entity_not_found",
            "source_id": source_id,
            "entity_id": entity_id,
            "sampled_at": sample.get("sampled_at"),
        }
    actual = str(entry.get("state") or "")
    match = actual.lower() == expected.lower()
    return ("success" if match else "failure"), {
        "source_id": source_id,
        "entity_id": entity_id,
        "expected_state": expected,
        "actual_state": actual,
        "last_changed": entry.get("last_changed"),
        "sampled_at": sample.get("sampled_at"),
    }


def _probe_weather_alert_active(probe: dict) -> tuple[str, dict]:
    """v0.5.23 (B17): rule fires when there's an active NWS alert for the
    configured weather source.

    Rule shape:
        probe = {"kind": "weather_alert_active",
                 "source_id": "ext_…",
                 "event_contains": "storm",     # optional substring filter
                 "min_severity": "Moderate",    # optional min severity
                 "max_sample_age_seconds": 600}

    Severity rank: Minor < Moderate < Severe < Extreme < Unknown
    (Unknown sorted last; treated as "any" if min_severity absent).
    """
    source_id = (probe.get("source_id") or "").strip()
    if not source_id:
        return "failure", {"reason": "missing source_id"}
    event_substr = (probe.get("event_contains") or "").strip().lower()
    min_sev_raw = (probe.get("min_severity") or "").strip()
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 600)
    except (TypeError, ValueError):
        max_age = 600

    sev_rank = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
    min_sev = sev_rank.get(min_sev_raw.lower(), 0)

    from app.services.external_sensors import latest_sample

    sample = latest_sample(source_id, max_age_seconds=max_age)
    if sample is None:
        return "failure", {
            "reason": "stale_sample",
            "source_id": source_id,
            "max_sample_age_seconds": max_age,
        }
    payload = sample.get("payload") or {}
    alerts = payload.get("alerts") if isinstance(payload, dict) else None
    if not isinstance(alerts, list):
        alerts = []
    matched: list[dict] = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        event = str(a.get("event") or "")
        sev = str(a.get("severity") or "").lower()
        if event_substr and event_substr not in event.lower():
            continue
        if min_sev and sev_rank.get(sev, 0) < min_sev:
            continue
        matched.append({
            "event": event,
            "severity": a.get("severity"),
            "headline": a.get("headline"),
            "ends": a.get("ends"),
        })
    return ("success" if matched else "failure"), {
        "source_id": source_id,
        "alerts_total": len(alerts),
        "alerts_matched": matched,
        "event_filter": event_substr or None,
        "min_severity": min_sev_raw or None,
        "sampled_at": sample.get("sampled_at"),
    }


def _probe_ical_event_active(probe: dict) -> tuple[str, dict]:
    """v0.5.23 (B17): rule fires when an iCal event matching `summary_contains`
    is currently airing (now ∈ [start, end)).

    Rule shape:
        probe = {"kind": "ical_event_active",
                 "source_id": "ext_…",
                 "summary_contains": "Jeopardy",
                 "max_sample_age_seconds": 1800}

    `summary_contains` is a case-insensitive substring against the event
    SUMMARY. If absent, ANY currently-airing event in the feed succeeds.
    """
    source_id = (probe.get("source_id") or "").strip()
    if not source_id:
        return "failure", {"reason": "missing source_id"}
    needle = (probe.get("summary_contains") or "").strip().lower()
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 1800)
    except (TypeError, ValueError):
        max_age = 1800

    from datetime import datetime as _dt, timezone as _tz
    from app.services.external_sensors import latest_sample

    sample = latest_sample(source_id, max_age_seconds=max_age)
    if sample is None:
        return "failure", {
            "reason": "stale_sample",
            "source_id": source_id,
            "max_sample_age_seconds": max_age,
        }
    payload = sample.get("payload") or {}
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        events = []
    now = _dt.now(_tz.utc)
    active: list[dict] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        summary = str(e.get("summary") or "")
        if needle and needle not in summary.lower():
            continue
        try:
            start = _dt.fromisoformat(str(e.get("start") or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        end_raw = e.get("end")
        try:
            end = _dt.fromisoformat(str(end_raw).replace("Z", "+00:00")) if end_raw else None
        except (TypeError, ValueError):
            end = None
        if end is None:
            # All-day or open-ended — treat as 24 h after start.
            from datetime import timedelta as _td
            end = start + _td(hours=24)
        if start <= now < end:
            active.append({"summary": summary, "start": e.get("start"), "end": e.get("end")})
    return ("success" if active else "failure"), {
        "source_id": source_id,
        "events_total": len(events),
        "events_active": active,
        "summary_filter": needle or None,
        "sampled_at": sample.get("sampled_at"),
    }
