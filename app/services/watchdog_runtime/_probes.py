"""Watchdog probe dispatcher + core network probes.

`run_probe(rule)` is the top-of-tick dispatcher. This module holds the
core network probes — internet / ping / tcp / http / dns — which use
only the stdlib (socket / urllib / http.client / subprocess).

The sensor-backed *integration* probes (Roku, HA, weather, iCal, power,
solar, SNMP, media, webhook, MQTT, EPG) live in the sibling
`_probes_integrations` module — `run_probe` dispatches into them.
v0.5.64 split: this file was 1265 LOC; the integration probes were
moved out so the dispatcher + core probes stay navigable.

Each `_probe_<kind>` returns either `bool` (legacy shape used
internally) or `tuple[str, dict]` — `(outcome, details)` where outcome
∈ {'success', 'failure'} and details is the event-log payload.
"""

from __future__ import annotations

import http.client
import re
import shutil
import socket
import subprocess
import urllib.parse

from app.models import WatchdogRule

# v0.5.64: the integration / sensor probes live in a sibling module.
# `run_probe` dispatches into them. One-directional import —
# `_probes_integrations` imports nothing from this module (its service
# imports are all deferred inside function bodies), so no cycle.
from app.services.watchdog_runtime._probes_integrations import (
    _probe_epg_show_airing,
    _probe_ha_numeric,
    _probe_ha_state_is,
    _probe_ical_event_active,
    _probe_media_session_active,
    _probe_mqtt_topic_equals,
    _probe_power,
    _probe_roku_app_active,
    _probe_snmp_error_rate,
    _probe_snmp_interface_down,
    _probe_snmp_throughput,
    _probe_solar,
    _probe_webhook_field_equals,
    _probe_weather_alert_active,
)

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

# v0.5.89 (BUG-058): every probe kind `run_probe` dispatches. This set
# MUST list exactly one entry per branch below — it is the runtime side
# of the canonical probe-kind registry. `tests/unit/test_probe_kind_registry.py`
# pins it equal to `app.models.watchdog.KNOWN_PROBE_KINDS` (the
# create-rule validation gate), so the two can no longer drift: a kind
# the runtime handles but validation rejects (or vice versa) fails CI.
DISPATCHED_PROBE_KINDS: frozenset[str] = frozenset({
    "internet", "ping", "tcp", "host_awake", "http", "dns", "gateway",
    "roku_app_active", "ha_state_is", "ha_numeric_above", "ha_numeric_below",
    "weather_alert_active", "ical_event_active",
    "power_above", "power_below", "power_zero_while_on",
    "solar_production_above", "solar_production_below",
    "snmp_interface_down", "snmp_throughput_above", "snmp_throughput_below",
    "snmp_error_rate_above", "media_session_active", "webhook_field_equals",
    "mqtt_topic_equals", "epg_show_airing",
})


def run_probe(rule: WatchdogRule) -> tuple[str, dict]:
    """Returns (outcome, details). outcome ∈ {'success', 'failure'}."""
    probe = rule.probe or {}
    kind = probe.get("kind")
    if kind not in DISPATCHED_PROBE_KINDS:
        return "failure", {"reason": f"unknown probe kind: {kind}"}
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
        if kind == "media_session_active":
            return _probe_media_session_active(probe)
        if kind == "webhook_field_equals":
            return _probe_webhook_field_equals(probe)
        if kind == "mqtt_topic_equals":
            return _probe_mqtt_topic_equals(probe)
        if kind == "epg_show_airing":
            return _probe_epg_show_airing(probe)
        if kind in ("tcp", "host_awake"):
            # v0.5.62 (B17 Ship 4): `host_awake` is a TCP-connect alias —
            # reachable = the host is powered on / awake. Defaults to
            # SSH port 22 (a common always-on service) when no port is
            # given. Probe succeeds when reachable, so a reboot rule
            # fires only while the host is OFF ("don't power-cycle the
            # office switch while the work laptop is on").
            default_port = 22 if kind == "host_awake" else 0
            ok = _probe_tcp(
                probe.get("host", ""),
                int(probe.get("port") or default_port),
            )
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
