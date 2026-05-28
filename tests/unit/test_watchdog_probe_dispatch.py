"""Unit tests — the watchdog probe dispatcher.

`app/services/watchdog_runtime/_probes.py` holds `run_probe(rule)` —
the top-of-tick dispatcher that routes a rule's `probe.kind` to a
concrete probe — plus the core network probes (internet / ping / tcp /
http / dns).

The probes themselves do real network I/O, so these tests cover the
*deterministic* surface: the dispatch table, the `_probe_internet`
target-validation logic, and every probe's no-input guard. Where a
test needs to exercise dispatch past the network call, the socket-level
`_probe_tcp` / `_probe_http` / `_probe_dns` helpers are monkeypatched —
no real packets, no `hub_db` needed.
"""

from __future__ import annotations

from app.models import WatchdogRule
from app.services.watchdog_runtime import _probes


def _rule(probe):
    """A WatchdogRule carrying just the `probe` dict — all `run_probe`
    reads. Built in-memory, never added to a session."""
    return WatchdogRule(probe=probe)


# ── run_probe dispatch table ───────────────────────────────────────────

def test_run_probe_unknown_kind_fails_with_reason():
    outcome, details = _probes.run_probe(_rule({"kind": "frobnicate"}))
    assert outcome == "failure"
    assert details == {"reason": "unknown probe kind: frobnicate"}


def test_run_probe_missing_kind_is_unknown():
    outcome, details = _probes.run_probe(_rule(None))
    assert outcome == "failure"
    assert details == {"reason": "unknown probe kind: None"}


def test_run_probe_gateway_is_skipped_as_success():
    outcome, details = _probes.run_probe(_rule({"kind": "gateway"}))
    assert outcome == "success"
    assert "skipped" in details


def test_run_probe_catches_probe_exception(monkeypatch):
    def boom(host, port):
        raise RuntimeError("socket layer exploded")

    monkeypatch.setattr(_probes, "_probe_tcp", boom)
    outcome, details = _probes.run_probe(_rule({"kind": "tcp", "host": "h", "port": 22}))
    assert outcome == "failure"
    assert details["reason"] == "probe_exception"
    assert "socket layer exploded" in details["error"]


def test_run_probe_tcp_maps_bool_to_outcome(monkeypatch):
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: True)
    assert _probes.run_probe(_rule({"kind": "tcp", "host": "h", "port": 22})) == ("success", {})
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: False)
    assert _probes.run_probe(_rule({"kind": "tcp", "host": "h", "port": 22})) == ("failure", {})


def test_run_probe_host_awake_defaults_to_port_22(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: seen.append((h, p)) or True)

    _probes.run_probe(_rule({"kind": "host_awake", "host": "laptop"}))
    _probes.run_probe(_rule({"kind": "tcp", "host": "switch"}))
    # host_awake with no port → SSH 22; plain tcp with no port → 0.
    assert seen == [("laptop", 22), ("switch", 0)]


def test_run_probe_http_and_dns_dispatch(monkeypatch):
    monkeypatch.setattr(_probes, "_probe_http", lambda url: True)
    monkeypatch.setattr(_probes, "_probe_dns", lambda host: False)
    assert _probes.run_probe(_rule({"kind": "http", "url": "http://x"})) == ("success", {})
    assert _probes.run_probe(_rule({"kind": "dns", "hostname": "x"})) == ("failure", {})


# ── _probe_internet — multi-target connectivity logic ──────────────────

def test_probe_internet_uses_default_targets_when_none_given(monkeypatch):
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: True)
    outcome, details = _probes._probe_internet({})
    assert outcome == "success"
    assert details["used_default_targets"] is True
    assert details["targets_total"] == len(_probes.DEFAULT_INTERNET_TARGETS)
    assert len(details["targets_succeeded"]) == len(_probes.DEFAULT_INTERNET_TARGETS)


def test_probe_internet_any_success_is_healthy(monkeypatch):
    # Only 8.8.8.8 reachable — ANY success = healthy.
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: h == "8.8.8.8")
    outcome, details = _probes._probe_internet({})
    assert outcome == "success"
    assert len(details["targets_succeeded"]) == 1
    assert len(details["targets_failed"]) == 2


def test_probe_internet_all_targets_fail_is_failure(monkeypatch):
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: False)
    outcome, details = _probes._probe_internet({})
    assert outcome == "failure"
    assert details["targets_succeeded"] == []
    assert len(details["targets_failed"]) == len(_probes.DEFAULT_INTERNET_TARGETS)


def test_probe_internet_rejects_malformed_targets():
    # None of these reach the socket layer — all fail target validation.
    bad = ["not-a-dict", {"port": 53}, {"host": "x", "port": 0},
           {"host": "y", "port": "abc"}]
    outcome, details = _probes._probe_internet({"targets": bad})
    assert outcome == "failure"
    assert details["targets_succeeded"] == []
    assert len(details["targets_failed"]) == 4
    assert all(f["error"] == "bad target shape" for f in details["targets_failed"])


def test_probe_internet_caps_target_count(monkeypatch):
    monkeypatch.setattr(_probes, "_probe_tcp", lambda h, p: False)
    many = [{"host": f"10.0.0.{i}", "port": 53} for i in range(20)]
    _, details = _probes._probe_internet({"targets": many})
    assert details["targets_total"] == _probes.MAX_INTERNET_TARGETS


# ── probe input guards (no network) ────────────────────────────────────

def test_probe_tcp_rejects_empty_host_or_port():
    assert _probes._probe_tcp("", 53) is False
    assert _probes._probe_tcp("host", 0) is False


def test_probe_http_rejects_empty_and_non_http_scheme():
    assert _probes._probe_http("") is False
    assert _probes._probe_http("ftp://example.com/file") is False


def test_probe_dns_rejects_empty_hostname():
    assert _probes._probe_dns("") is False


def test_probe_ping_missing_host():
    outcome, details = _probes._probe_ping({})
    assert outcome == "failure"
    assert details == {"reason": "missing host"}


# ── device_heartbeat_stale (fleet-presence) ────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import Device  # noqa: E402


def test_device_heartbeat_stale_missing_device_id():
    outcome, details = _probes.run_probe(_rule({"kind": "device_heartbeat_stale"}))
    assert outcome == "failure"
    assert details["reason"] == "missing device_id"


def test_device_heartbeat_stale_no_heartbeat_fails(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-x"))  # last_heartbeat_at is NULL
    outcome, details = _probes.run_probe(
        _rule({"kind": "device_heartbeat_stale", "device_id": "dev-x"})
    )
    assert outcome == "failure"
    assert details["reason"] == "no_heartbeat"


def test_device_heartbeat_stale_recent_is_success(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(Device(id="dev-x", last_heartbeat_at=now - timedelta(seconds=30)))
    outcome, details = _probes.run_probe(
        _rule({"kind": "device_heartbeat_stale", "device_id": "dev-x",
               "max_age_seconds": 300})
    )
    assert outcome == "success"
    assert details["age_seconds"] < 300


def test_device_heartbeat_stale_old_is_failure(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(Device(id="dev-x", last_heartbeat_at=now - timedelta(seconds=900)))
    outcome, details = _probes.run_probe(
        _rule({"kind": "device_heartbeat_stale", "device_id": "dev-x",
               "max_age_seconds": 300})
    )
    assert outcome == "failure"
    assert details["reason"] == "heartbeat_stale"
    assert details["age_seconds"] > 300


def test_device_heartbeat_stale_default_window_300s(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        # 240s old, no max_age given -> default 300 -> still success
        s.add(Device(id="dev-x", last_heartbeat_at=now - timedelta(seconds=240)))
    outcome, _ = _probes.run_probe(
        _rule({"kind": "device_heartbeat_stale", "device_id": "dev-x"})
    )
    assert outcome == "success"
