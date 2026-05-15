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
        if kind in ("ha_numeric_above", "ha_numeric_below"):
            return _probe_ha_numeric(probe, kind)
        if kind == "weather_alert_active":
            return _probe_weather_alert_active(probe)
        if kind == "ical_event_active":
            return _probe_ical_event_active(probe)
        if kind in ("power_above", "power_below", "power_zero_while_on"):
            return _probe_power(probe, kind)
        if kind in ("solar_production_above", "solar_production_below"):
            return _probe_solar(probe, kind)
        if kind == "snmp_interface_down":
            return _probe_snmp_interface_down(probe)
        if kind in ("snmp_throughput_above", "snmp_throughput_below"):
            return _probe_snmp_throughput(probe, kind)
        if kind == "snmp_error_rate_above":
            return _probe_snmp_error_rate(probe)
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


def _probe_ha_numeric(probe: dict, kind: str) -> tuple[str, dict]:
    """v0.5.57 (P2.4): numeric-threshold probe over a Home Assistant
    entity — the deepening of the string-only `ha_state_is`.

    Rule shape:
        probe = {"kind": "ha_numeric_above" | "ha_numeric_below",
                 "source_id": "ext_…",
                 "entity_id": "sensor.freezer_temp",
                 "attribute": null,        # optional — read this
                                           # attribute instead of `state`
                 "threshold": -10,
                 "max_sample_age_seconds": 120}

    The HA poll already caches every entity's `state` + `attributes`;
    this probe makes the numeric ones (temperature, humidity, battery %,
    power …) usable for rules. Many HA entities carry the real value in
    an attribute (e.g. `climate.*` → `current_temperature`), so
    `attribute` optionally redirects the read.

    Semantics mirror `power_above`/`power_below` — "failure" (builds
    toward firing the action) on the actionable condition:
    - ha_numeric_above → fails when value > threshold
    - ha_numeric_below → fails when value < threshold
    Non-numeric / missing readings fail with an explanatory `reason`.
    """
    source_id = (probe.get("source_id") or "").strip()
    entity_id = (probe.get("entity_id") or "").strip()
    if not source_id or not entity_id:
        return "failure", {"reason": "missing source_id / entity_id"}
    attribute = (probe.get("attribute") or "").strip() or None
    try:
        threshold = float(probe.get("threshold"))
    except (TypeError, ValueError):
        return "failure", {"reason": "missing or non-numeric threshold"}
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
    if attribute:
        raw = (entry.get("attributes") or {}).get(attribute)
    else:
        raw = entry.get("state")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "failure", {
            "reason": "non_numeric_reading",
            "source_id": source_id,
            "entity_id": entity_id,
            "attribute": attribute,
            "raw_value": raw,
            "sampled_at": sample.get("sampled_at"),
        }

    details = {
        "source_id": source_id,
        "entity_id": entity_id,
        "attribute": attribute,
        "value": value,
        "threshold": threshold,
        "last_changed": entry.get("last_changed"),
        "sampled_at": sample.get("sampled_at"),
    }
    if kind == "ha_numeric_above":
        return ("failure" if value > threshold else "success"), details
    # ha_numeric_below
    return ("failure" if value < threshold else "success"), details


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


def _probe_power(probe: dict, kind: str) -> tuple[str, dict]:
    """v0.5.32 (B16 Phase 1D): power-telemetry probe.

    Rule shape:
        probe = {"kind": "power_above" | "power_below" | "power_zero_while_on",
                 "device_id": "dev_…",
                 "threshold_w": 1500,              # power_above / power_below
                 "near_zero_threshold_w": 0.5,    # power_zero_while_on
                 "window_seconds": 300}           # avg over window

    Semantics — "fails" (and thus eventually fires the rule's action)
    when the unhealthy condition is detected:
    - power_above       → fails when avg_w(window) > threshold_w
    - power_below       → fails when avg_w(window) < threshold_w
                          (requires sample_count > 0 — empty window
                          returns success so a dead poller doesn't
                          trigger a power-cycle)
    - power_zero_while_on → fails when relay_on=True (latest heartbeat)
                          AND avg_w(window) < near_zero_threshold_w
                          (catches "appliance died but relay still
                          energized" — the operator's classic phantom-
                          failure case)

    Stale-sample failure gate: same as the external-sensor probes —
    if the most-recent sample is older than `max_sample_age_seconds`
    (default 600s = 10 min), the probe returns failure with
    `reason='stale_sample'` so a dead device-side sampler can never
    pin a rule to a misleading "success" state.
    """
    device_id = (probe.get("device_id") or "").strip()
    if not device_id:
        return "failure", {"reason": "missing device_id"}
    try:
        window = int(probe.get("window_seconds") or 300)
    except (TypeError, ValueError):
        window = 300
    if window < 30:
        window = 30
    if window > 86400:
        window = 86400

    try:
        max_age = int(probe.get("max_sample_age_seconds") or 600)
    except (TypeError, ValueError):
        max_age = 600

    # Deferred import — keeps watchdog runtime self-contained for
    # callers that don't use power probes.
    from app.services import device_power

    latest = device_power.latest_sample(device_id)
    if latest is None:
        return "failure", {
            "reason": "no_samples",
            "device_id": device_id,
        }
    if (latest.get("age_seconds") or 0) > max_age:
        return "failure", {
            "reason": "stale_sample",
            "device_id": device_id,
            "sample_age_seconds": latest.get("age_seconds"),
            "max_sample_age_seconds": max_age,
        }

    # Compute window aggregate. Cheap — RECENT_WINDOW_DEFAULT_SECONDS
    # already supports up-to-24h windows; we use that path.
    recent = device_power.recent_samples(device_id, window_seconds=window)
    if not recent:
        return "failure", {
            "reason": "no_samples_in_window",
            "device_id": device_id,
            "window_seconds": window,
        }
    vals = [r["p_w"] for r in recent if r.get("p_w") is not None]
    if not vals:
        # Samples exist but no p_w reading — device reports rssi but
        # not real-power (e.g. firmware without CSE7766 wiring).
        return "failure", {
            "reason": "no_real_power_readings",
            "device_id": device_id,
            "sample_count": len(recent),
        }
    avg_w = sum(vals) / len(vals)

    details = {
        "device_id": device_id,
        "window_seconds": window,
        "sample_count": len(recent),
        "avg_w": round(avg_w, 3),
        "min_w": round(min(vals), 3),
        "max_w": round(max(vals), 3),
        "latest_sample_age_seconds": latest.get("age_seconds"),
    }

    if kind == "power_above":
        try:
            threshold = float(probe.get("threshold_w"))
        except (TypeError, ValueError):
            return "failure", {**details, "reason": "missing threshold_w"}
        details["threshold_w"] = threshold
        # "success" means the rule is healthy (i.e. NOT above).
        # "failure" means we crossed the threshold and the rule should
        # build toward firing.
        return ("failure" if avg_w > threshold else "success"), details

    if kind == "power_below":
        try:
            threshold = float(probe.get("threshold_w"))
        except (TypeError, ValueError):
            return "failure", {**details, "reason": "missing threshold_w"}
        details["threshold_w"] = threshold
        return ("failure" if avg_w < threshold else "success"), details

    if kind == "power_zero_while_on":
        try:
            near_zero = float(probe.get("near_zero_threshold_w") or 0.5)
        except (TypeError, ValueError):
            near_zero = 0.5
        details["near_zero_threshold_w"] = near_zero
        relay_on = bool(latest.get("p_w") is not None and (
            latest.get("p_w") or 0
        ) > near_zero or latest.get("relay_on"))
        # The truer signal: device_heartbeat's relay_on. Pull it
        # directly to avoid mis-attributing failure to a transient.
        from app.db import session_scope as _ss
        from sqlalchemy import select as _select
        from app.models import DeviceHeartbeat as _DH

        with _ss() as session:
            hb = session.scalar(
                _select(_DH)
                .where(_DH.device_id == device_id)
                .order_by(_DH.received_at.desc())
                .limit(1)
            )
            relay_on_heartbeat = bool(hb.relay_on) if hb else False
        details["relay_on"] = relay_on_heartbeat
        if relay_on_heartbeat and avg_w < near_zero:
            return "failure", details
        return "success", details

    return "failure", {"reason": f"unknown power probe kind: {kind}", **details}


def _probe_solar(probe: dict, kind: str) -> tuple[str, dict]:
    """v0.5.56 (P2.1): solar-production probe over a SolarEdge /
    Enphase-Envoy external sensor source.

    Rule shape:
        probe = {"kind": "solar_production_above" | "solar_production_below",
                 "source_id": "ext_…",
                 "threshold_w": 3000,
                 "max_sample_age_seconds": 1800}

    Mirrors the B16 `power_above`/`power_below` semantics — "failure"
    (builds toward firing the rule's action) on the actionable
    condition:
    - solar_production_above → fails when production_w > threshold_w
      (e.g. "exporting > 3 kW → switch on the water heater")
    - solar_production_below → fails when production_w < threshold_w

    Stale-sample gate: solar sources poll every ~5 min, so if the most
    recent sample is older than `max_sample_age_seconds` (default 1800s
    = 30 min) the probe fails with `reason='stale_sample'` rather than
    acting on a stale generation reading.
    """
    source_id = (probe.get("source_id") or "").strip()
    if not source_id:
        return "failure", {"reason": "missing source_id"}
    try:
        threshold = float(probe.get("threshold_w"))
    except (TypeError, ValueError):
        return "failure", {"reason": "missing threshold_w", "source_id": source_id}
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 1800)
    except (TypeError, ValueError):
        max_age = 1800

    from app.services.external_sensors import latest_sample

    sample = latest_sample(source_id, max_age_seconds=max_age)
    if sample is None:
        return "failure", {
            "reason": "stale_sample",
            "source_id": source_id,
            "max_sample_age_seconds": max_age,
        }
    payload = sample.get("payload") or {}
    production_w = payload.get("production_w") if isinstance(payload, dict) else None
    if production_w is None:
        return "failure", {
            "reason": "no_production_reading",
            "source_id": source_id,
            "sampled_at": sample.get("sampled_at"),
        }
    try:
        production_w = float(production_w)
    except (TypeError, ValueError):
        return "failure", {"reason": "bad_production_reading", "source_id": source_id}

    details = {
        "source_id": source_id,
        "production_w": round(production_w, 1),
        "threshold_w": threshold,
        "vendor": payload.get("vendor"),
        "sampled_at": sample.get("sampled_at"),
    }
    if kind == "solar_production_above":
        return ("failure" if production_w > threshold else "success"), details
    # solar_production_below
    return ("failure" if production_w < threshold else "success"), details


def _probe_snmp_interface_down(probe: dict) -> tuple[str, dict]:
    """v0.5.58 (P2.2/P2.3): point-in-time link-state probe over an SNMP
    external sensor source.

    Rule shape:
        probe = {"kind": "snmp_interface_down",
                 "source_id": "ext_…",
                 "interface": "wan",
                 "max_sample_age_seconds": 600}

    "failure" (builds toward the rule's action) when the named
    interface's `oper_status` is not `up` — the WAN-down detector.
    Pair with a `relay_cycle` action on the modem's plug.
    """
    source_id = (probe.get("source_id") or "").strip()
    interface = (probe.get("interface") or "").strip()
    if not source_id or not interface:
        return "failure", {"reason": "missing source_id / interface"}
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 600)
    except (TypeError, ValueError):
        max_age = 600

    from app.services.external_sensors import latest_sample

    sample = latest_sample(source_id, max_age_seconds=max_age)
    if sample is None:
        return "failure", {
            "reason": "stale_sample",
            "source_id": source_id,
            "max_sample_age_seconds": max_age,
        }
    interfaces = (sample.get("payload") or {}).get("interfaces") or {}
    entry = interfaces.get(interface) if isinstance(interfaces, dict) else None
    if not isinstance(entry, dict):
        return "failure", {
            "reason": "interface_not_found",
            "source_id": source_id,
            "interface": interface,
            "sampled_at": sample.get("sampled_at"),
        }
    status = entry.get("oper_status")
    return ("success" if status == "up" else "failure"), {
        "source_id": source_id,
        "interface": interface,
        "oper_status": status,
        "sampled_at": sample.get("sampled_at"),
    }


def _snmp_pair_interface(probe: dict, default_max_age: int = 600):
    """Shared front-half of the SNMP rate probes: resolve the source +
    interface and fetch the (newer, older) sample pair.

    Returns either a ``(outcome, details)`` early-return tuple — caller
    should return it verbatim — or ``(None, (newer_if, older_if, dt))``
    when the pair is usable.
    """
    source_id = (probe.get("source_id") or "").strip()
    interface = (probe.get("interface") or "").strip()
    if not source_id or not interface:
        return ("failure", {"reason": "missing source_id / interface"}), None
    try:
        max_age = int(probe.get("max_sample_age_seconds") or default_max_age)
    except (TypeError, ValueError):
        max_age = default_max_age

    from app.services.external_sensors import last_two_samples

    pair = last_two_samples(source_id, max_age_seconds=max_age)
    if pair is None:
        # One sample (cold start) or stale — not actionable. Succeed so a
        # fresh source doesn't fire a rule before it has history.
        return ("success", {
            "reason": "insufficient_history",
            "source_id": source_id,
        }), None
    newer, older = pair

    def _iface(s):
        ifaces = (s.get("payload") or {}).get("interfaces") or {}
        return ifaces.get(interface) if isinstance(ifaces, dict) else None

    n_if, o_if = _iface(newer), _iface(older)
    if not isinstance(n_if, dict) or not isinstance(o_if, dict):
        return ("failure", {
            "reason": "interface_not_found",
            "source_id": source_id,
            "interface": interface,
        }), None
    dt = (newer["sampled_at"] - older["sampled_at"]).total_seconds()
    if dt <= 0:
        return ("success", {"reason": "bad_interval", "source_id": source_id}), None
    return None, (n_if, o_if, dt, newer["sampled_at"])


def _snmp_counter_delta(n_if: dict, o_if: dict, field: str) -> int | None:
    """Monotonic-counter delta; None on a missing reading or a counter
    reset (newer < older — device reboot / agent restart)."""
    n, o = n_if.get(field), o_if.get(field)
    if n is None or o is None:
        return None
    delta = n - o
    return delta if delta >= 0 else None


def _probe_snmp_throughput(probe: dict, kind: str) -> tuple[str, dict]:
    """v0.5.58 (P2.2/P2.3): interface throughput probe — bits/sec from
    the octet-counter delta between the last two samples.

    Rule shape:
        probe = {"kind": "snmp_throughput_above" | "snmp_throughput_below",
                 "source_id": "ext_…",
                 "interface": "wan",
                 "direction": "in" | "out" | "total",
                 "threshold_bps": 1000000,
                 "max_sample_age_seconds": 600}

    `snmp_throughput_below` is the "link is up but carrying no traffic —
    likely wedged" signal that bare link-state misses.
    """
    direction = (probe.get("direction") or "total").strip().lower()
    if direction not in ("in", "out", "total"):
        return "failure", {"reason": "direction must be in / out / total"}
    try:
        threshold = float(probe.get("threshold_bps"))
    except (TypeError, ValueError):
        return "failure", {"reason": "missing or non-numeric threshold_bps"}

    early, usable = _snmp_pair_interface(probe)
    if early is not None:
        return early
    n_if, o_if, dt, sampled_at = usable

    if direction == "in":
        octet_delta = _snmp_counter_delta(n_if, o_if, "in_octets")
    elif direction == "out":
        octet_delta = _snmp_counter_delta(n_if, o_if, "out_octets")
    else:
        d_in = _snmp_counter_delta(n_if, o_if, "in_octets")
        d_out = _snmp_counter_delta(n_if, o_if, "out_octets")
        octet_delta = (
            d_in + d_out if (d_in is not None and d_out is not None) else None
        )
    if octet_delta is None:
        return "success", {
            "reason": "counter_reset",
            "source_id": (probe.get("source_id") or "").strip(),
            "interface": (probe.get("interface") or "").strip(),
        }
    bps = octet_delta * 8 / dt
    details = {
        "source_id": (probe.get("source_id") or "").strip(),
        "interface": (probe.get("interface") or "").strip(),
        "direction": direction,
        "throughput_bps": round(bps, 1),
        "threshold_bps": threshold,
        "interval_seconds": round(dt, 1),
        "sampled_at": sampled_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if kind == "snmp_throughput_above":
        return ("failure" if bps > threshold else "success"), details
    # snmp_throughput_below
    return ("failure" if bps < threshold else "success"), details


def _probe_snmp_error_rate(probe: dict) -> tuple[str, dict]:
    """v0.5.58 (P2.2/P2.3): interface error-rate probe — RX+TX error
    counters per minute from the delta between the last two samples.
    Catches a flaky cable / dying port (the P2.3 per-port story).

    Rule shape:
        probe = {"kind": "snmp_error_rate_above",
                 "source_id": "ext_…",
                 "interface": "lan3",
                 "threshold_errors_per_min": 10,
                 "max_sample_age_seconds": 600}
    """
    try:
        threshold = float(probe.get("threshold_errors_per_min"))
    except (TypeError, ValueError):
        return "failure", {"reason": "missing or non-numeric threshold_errors_per_min"}

    early, usable = _snmp_pair_interface(probe)
    if early is not None:
        return early
    n_if, o_if, dt, sampled_at = usable

    d_in = _snmp_counter_delta(n_if, o_if, "in_errors")
    d_out = _snmp_counter_delta(n_if, o_if, "out_errors")
    if d_in is None or d_out is None:
        return "success", {
            "reason": "counter_reset",
            "source_id": (probe.get("source_id") or "").strip(),
            "interface": (probe.get("interface") or "").strip(),
        }
    errors_per_min = (d_in + d_out) / dt * 60.0
    details = {
        "source_id": (probe.get("source_id") or "").strip(),
        "interface": (probe.get("interface") or "").strip(),
        "errors_per_min": round(errors_per_min, 2),
        "threshold_errors_per_min": threshold,
        "interval_seconds": round(dt, 1),
        "sampled_at": sampled_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return ("failure" if errors_per_min > threshold else "success"), details
