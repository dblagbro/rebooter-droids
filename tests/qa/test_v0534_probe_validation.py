"""v0.5.34 (BUG-054 + BUG-055): per-kind probe validation regression tests.

Two bugs the 2026-05-15 deep regression sweep surfaced:

- **BUG-054** — `PROBE_KIND_CUSTOM` was in `KNOWN_PROBE_KINDS` since
  v0.4.0 but `_run_probe` never had a handler. Operators could save
  a rule that the runtime returned `failure: reason='unknown probe
  kind: custom'` for every tick. v0.5.34 drops `custom` from the
  canonical list.

- **BUG-055** — `create_rule` only validated `probe.kind ∈
  KNOWN_PROBE_KINDS` and (for `internet`) the `targets` list shape.
  All other per-kind required fields were unvalidated, so e.g.
  `{"kind":"power_above","threshold_w":"oops"}` returned 201. v0.5.34
  adds a `_validate_probe()` per-kind dispatcher.

This file is the regression net. If either fix regresses, these
tests fail loud.
"""

from __future__ import annotations

import pytest
import requests

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



# ── BUG-054 retest ─────────────────────────────────────────────────────


def test_custom_probe_kind_rejected_at_create(base_url, admin_headers):
    """v0.5.34: `custom` is no longer in KNOWN_PROBE_KINDS — the
    create-rule API should reject it with 400 validation_failed."""
    body = {
        "name": "qa-v0534-custom",
        "probe": {"kind": "custom", "name": "qa"},
        "target": {"kind": "tag", "tag": "qa"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3,
        "recovery_threshold": 2,
        "window_seconds": 60,
        "cooldown_seconds": 300,
    }
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers={**admin_headers, "Content-Type": "application/json"},
        json=body,
        timeout=10,
    )
    assert r.status_code == 400, (
        f"`custom` probe kind should be rejected (BUG-054 fix); got {r.status_code}: {r.text[:200]}"
    )
    err = r.json().get("error", {}).get("message", "")
    assert "probe.kind must be one of" in err
    assert "custom" not in err.split(" must be one of ")[-1], (
        "custom should not appear in the allowed-kinds list"
    )


# ── BUG-055 retest — parametrised negative cases ───────────────────────


BAD_CASES = [
    # (label, probe_dict, expected_message_substring)
    # roku_app_active
    ("roku-missing-source_id",
     {"kind": "roku_app_active", "app_name": "x"},
     "probe.source_id is required"),
    ("roku-missing-app_name",
     {"kind": "roku_app_active", "source_id": "ext_x"},
     "probe.app_name is required"),
    ("roku-empty-app_name",
     {"kind": "roku_app_active", "source_id": "ext_x", "app_name": ""},
     "probe.app_name is required"),
    # ha_state_is
    ("ha-missing-source_id",
     {"kind": "ha_state_is", "entity_id": "sensor.x", "expected_state": "on"},
     "probe.source_id is required"),
    ("ha-missing-entity_id",
     {"kind": "ha_state_is", "source_id": "ext_x", "expected_state": "on"},
     "probe.entity_id is required"),
    ("ha-missing-expected_state",
     {"kind": "ha_state_is", "source_id": "ext_x", "entity_id": "sensor.x"},
     "probe.expected_state is required"),
    # weather_alert_active
    ("weather-missing-source_id",
     {"kind": "weather_alert_active"},
     "probe.source_id is required"),
    ("weather-bogus-severity",
     {"kind": "weather_alert_active", "source_id": "ext_x", "min_severity": "Catastrophic"},
     "probe.min_severity must be one of"),
    # ical_event_active
    ("ical-missing-source_id",
     {"kind": "ical_event_active"},
     "probe.source_id is required"),
    # power_above / power_below
    ("power_above-missing-device_id",
     {"kind": "power_above", "threshold_w": 100, "window_seconds": 300},
     "probe.device_id is required"),
    ("power_above-missing-threshold",
     {"kind": "power_above", "device_id": "dev_x", "window_seconds": 300},
     "probe.threshold_w is required"),
    ("power_above-string-threshold",
     {"kind": "power_above", "device_id": "dev_x", "threshold_w": "oops"},
     "probe.threshold_w must be numeric"),
    ("power_above-negative-threshold",
     {"kind": "power_above", "device_id": "dev_x", "threshold_w": -1},
     "must be between"),
    ("power_above-huge-threshold",
     {"kind": "power_above", "device_id": "dev_x", "threshold_w": 999999},
     "must be between"),
    ("power_above-tiny-window",
     {"kind": "power_above", "device_id": "dev_x", "threshold_w": 100, "window_seconds": 1},
     "must be between"),
    ("power_above-huge-window",
     {"kind": "power_above", "device_id": "dev_x", "threshold_w": 100, "window_seconds": 99999999},
     "must be between"),
    ("power_below-missing-device_id",
     {"kind": "power_below", "threshold_w": 5},
     "probe.device_id is required"),
    # power_zero_while_on
    ("power_zero-missing-device_id",
     {"kind": "power_zero_while_on"},
     "probe.device_id is required"),
    ("power_zero-bad-near_zero",
     {"kind": "power_zero_while_on", "device_id": "dev_x", "near_zero_threshold_w": "wat"},
     "must be numeric"),
    # tcp / http / dns / ping smoke
    ("ping-missing-host",
     {"kind": "ping"},
     "probe.host is required"),
    ("tcp-missing-port",
     {"kind": "tcp", "host": "1.1.1.1"},
     "probe.port is required"),
    ("tcp-bad-port",
     {"kind": "tcp", "host": "1.1.1.1", "port": 99999},
     "must be between"),
    ("http-missing-url",
     {"kind": "http"},
     "probe.url is required"),
    ("http-bad-scheme",
     {"kind": "http", "url": "ftp://example.com"},
     "must use http:// or https://"),
    ("dns-missing-hostname",
     {"kind": "dns"},
     "probe.hostname is required"),
]


@pytest.mark.parametrize("label,probe,expected_msg", BAD_CASES, ids=[c[0] for c in BAD_CASES])
def test_bad_probe_rejected_at_create(base_url, admin_headers, label, probe, expected_msg):
    """v0.5.34: every bad-shape probe configuration must return 400
    with a per-kind error message. Pre-v0.5.34 these all returned 201
    and the rules silently failed at runtime (BUG-055)."""
    body = {
        "name": f"qa-v0534-{label}",
        "probe": probe,
        "target": {"kind": "tag", "tag": "qa"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3,
        "recovery_threshold": 2,
        "window_seconds": 60,
        "cooldown_seconds": 300,
    }
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers={**admin_headers, "Content-Type": "application/json"},
        json=body,
        timeout=10,
    )
    if r.status_code == 201:
        # Cleanup the unexpectedly-created rule before failing the test.
        rid = r.json()["data"]["id"]
        requests.delete(
            f"{base_url}/api/v1/admin/rules/{rid}",
            headers=admin_headers,
            timeout=10,
        )
        pytest.fail(
            f"{label}: expected 400 with '{expected_msg}', got 201 — "
            f"BUG-055 regression"
        )
    assert r.status_code == 400, f"{label}: status={r.status_code} body={r.text[:200]}"
    err = r.json().get("error", {}).get("message", "")
    assert expected_msg in err, (
        f"{label}: expected message to contain {expected_msg!r}, got: {err!r}"
    )


# ── Happy path: valid configurations still work ────────────────────────


GOOD_CASES = [
    ("internet-default",
     {"kind": "internet"}),
    ("internet-with-targets",
     {"kind": "internet", "targets": [{"host": "1.1.1.1", "port": 53}]}),
    ("ping",
     {"kind": "ping", "host": "1.1.1.1"}),
    ("tcp",
     {"kind": "tcp", "host": "1.1.1.1", "port": 53}),
    ("http",
     {"kind": "http", "url": "https://example.com"}),
    ("dns",
     {"kind": "dns", "hostname": "example.com"}),
    ("gateway",
     {"kind": "gateway"}),
    ("roku",
     {"kind": "roku_app_active", "source_id": "ext_qa", "app_name": "x"}),
    ("ha",
     {"kind": "ha_state_is", "source_id": "ext_qa", "entity_id": "sensor.x", "expected_state": "on"}),
    ("weather-minimal",
     {"kind": "weather_alert_active", "source_id": "ext_qa"}),
    ("weather-with-severity",
     {"kind": "weather_alert_active", "source_id": "ext_qa", "min_severity": "Severe"}),
    ("ical",
     {"kind": "ical_event_active", "source_id": "ext_qa"}),
    ("power_above",
     {"kind": "power_above", "device_id": "dev_x", "threshold_w": 100, "window_seconds": 300}),
    ("power_below",
     {"kind": "power_below", "device_id": "dev_x", "threshold_w": 5, "window_seconds": 300}),
    ("power_zero",
     {"kind": "power_zero_while_on", "device_id": "dev_x", "near_zero_threshold_w": 0.5}),
]


@pytest.mark.parametrize("label,probe", GOOD_CASES, ids=[c[0] for c in GOOD_CASES])
def test_valid_probe_accepted_at_create(base_url, admin_headers, label, probe):
    """v0.5.34: valid configurations across all 13 canonical kinds
    still get 201 — the per-kind validator must not block well-formed
    rules."""
    body = {
        "name": f"qa-v0534-good-{label}",
        "probe": probe,
        "target": {"kind": "tag", "tag": "qa"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3,
        "recovery_threshold": 2,
        "window_seconds": 60,
        "cooldown_seconds": 300,
    }
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers={**admin_headers, "Content-Type": "application/json"},
        json=body,
        timeout=10,
    )
    rid = None
    try:
        assert r.status_code == 201, f"{label}: status={r.status_code} body={r.text[:200]}"
        rid = r.json()["data"]["id"]
    finally:
        if rid:
            requests.delete(
                f"{base_url}/api/v1/admin/rules/{rid}",
                headers=admin_headers,
                timeout=10,
            )
