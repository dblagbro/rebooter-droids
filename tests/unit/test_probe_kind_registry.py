"""Unit tests — the canonical probe-kind registry (BUG-058).

`KNOWN_PROBE_KINDS` (the `create_rule` validation gate, in
`app.models.watchdog`) and `DISPATCHED_PROBE_KINDS` (what
`watchdog_runtime._probes.run_probe` actually handles) must stay
identical. A kind in one but not the other *is* BUG-058 — the runtime
supported ~26 kinds while validation accepted only 13, so a dozen
runtime-supported probe kinds could not be created via the API or the
JSON editor.

This file pins that contract and checks `_validate_probe` has a
working branch for every canonical kind.
"""

from __future__ import annotations

import pytest

from app.models.watchdog import KNOWN_PROBE_KINDS
from app.services.watchdog import WatchdogValidationError, _validate_probe
from app.services.watchdog_runtime._probes import DISPATCHED_PROBE_KINDS


# A complete, well-formed probe dict for every canonical kind. Kept in
# lockstep with KNOWN_PROBE_KINDS by `test_fixture_covers_every_kind`.
_VALID_PROBES: dict[str, dict] = {
    "internet": {"kind": "internet"},
    "ping": {"kind": "ping", "host": "1.1.1.1"},
    "tcp": {"kind": "tcp", "host": "10.0.0.1", "port": 22},
    "host_awake": {"kind": "host_awake", "host": "10.0.0.1"},
    "http": {"kind": "http", "url": "https://example.com"},
    "dns": {"kind": "dns", "hostname": "example.com"},
    "gateway": {"kind": "gateway"},
    "roku_app_active": {
        "kind": "roku_app_active", "source_id": "s", "app_name": "Netflix",
    },
    "ha_state_is": {
        "kind": "ha_state_is", "source_id": "s",
        "entity_id": "sensor.x", "expected_state": "on",
    },
    "ha_numeric_above": {
        "kind": "ha_numeric_above", "source_id": "s",
        "entity_id": "sensor.x", "threshold": 20,
    },
    "ha_numeric_below": {
        "kind": "ha_numeric_below", "source_id": "s",
        "entity_id": "sensor.x", "threshold": 5,
    },
    "weather_alert_active": {"kind": "weather_alert_active", "source_id": "s"},
    "ical_event_active": {"kind": "ical_event_active", "source_id": "s"},
    "power_above": {"kind": "power_above", "device_id": "d", "threshold_w": 100},
    "power_below": {"kind": "power_below", "device_id": "d", "threshold_w": 5},
    "power_zero_while_on": {"kind": "power_zero_while_on", "device_id": "d"},
    "solar_production_above": {
        "kind": "solar_production_above", "source_id": "s", "threshold_w": 1000,
    },
    "solar_production_below": {
        "kind": "solar_production_below", "source_id": "s", "threshold_w": 100,
    },
    "snmp_interface_down": {
        "kind": "snmp_interface_down", "source_id": "s", "interface": "eth0",
    },
    "snmp_throughput_above": {
        "kind": "snmp_throughput_above", "source_id": "s",
        "interface": "eth0", "threshold_bps": 1_000_000,
    },
    "snmp_throughput_below": {
        "kind": "snmp_throughput_below", "source_id": "s",
        "interface": "eth0", "threshold_bps": 1_000,
    },
    "snmp_error_rate_above": {
        "kind": "snmp_error_rate_above", "source_id": "s",
        "interface": "eth0", "threshold_errors_per_min": 10,
    },
    "media_session_active": {"kind": "media_session_active", "source_id": "s"},
    "webhook_field_equals": {
        "kind": "webhook_field_equals", "source_id": "s", "field": "state",
    },
    "mqtt_topic_equals": {
        "kind": "mqtt_topic_equals", "source_id": "s", "topic": "home/x",
    },
    "epg_show_airing": {"kind": "epg_show_airing", "show": "Jeopardy"},
}

# (kind, field-to-drop) — dropping a required field must be rejected.
_MISSING_FIELD_CASES = [
    ("host_awake", "host"),
    ("ha_numeric_above", "source_id"),
    ("ha_numeric_above", "entity_id"),
    ("ha_numeric_above", "threshold"),
    ("ha_numeric_below", "threshold"),
    ("solar_production_above", "source_id"),
    ("solar_production_above", "threshold_w"),
    ("snmp_interface_down", "interface"),
    ("snmp_throughput_above", "interface"),
    ("snmp_throughput_above", "threshold_bps"),
    ("snmp_error_rate_above", "threshold_errors_per_min"),
    ("media_session_active", "source_id"),
    ("webhook_field_equals", "field"),
    ("mqtt_topic_equals", "topic"),
    ("epg_show_airing", "show"),
]


# ── the BUG-058 contract ───────────────────────────────────────────────

def test_known_kinds_equals_dispatched_kinds():
    """The core contract: the create-rule validation gate and the
    runtime dispatcher accept exactly the same set of probe kinds."""
    assert set(KNOWN_PROBE_KINDS) == DISPATCHED_PROBE_KINDS


def test_known_kinds_has_no_duplicates():
    assert len(KNOWN_PROBE_KINDS) == len(set(KNOWN_PROBE_KINDS))


def test_registry_size():
    # 6 core network + host_awake + 4 HA/roku + 2 weather/ical
    # + 3 power + 2 solar + 4 snmp + media + webhook + mqtt + epg.
    assert len(KNOWN_PROBE_KINDS) == 26


def test_fixture_covers_every_kind():
    """Guards this test file against registry drift — every canonical
    kind needs a valid-probe fixture below."""
    assert set(_VALID_PROBES) == set(KNOWN_PROBE_KINDS)


# ── _validate_probe — every canonical kind has a working branch ────────

@pytest.mark.parametrize("kind", sorted(_VALID_PROBES))
def test_validate_probe_accepts_well_formed_probe(kind):
    # A well-formed probe returns None. If a kind were in
    # KNOWN_PROBE_KINDS without a _validate_probe branch, the
    # fail-closed default ("no validator") would raise here.
    assert _validate_probe(_VALID_PROBES[kind]) is None


@pytest.mark.parametrize("kind, missing", _MISSING_FIELD_CASES)
def test_validate_probe_rejects_missing_required_field(kind, missing):
    probe = {k: v for k, v in _VALID_PROBES[kind].items() if k != missing}
    with pytest.raises(WatchdogValidationError):
        _validate_probe(probe)


def test_validate_probe_rejects_non_numeric_ha_threshold():
    bad = dict(_VALID_PROBES["ha_numeric_above"], threshold="not-a-number")
    with pytest.raises(WatchdogValidationError):
        _validate_probe(bad)


def test_validate_probe_rejects_non_numeric_snmp_threshold():
    bad = dict(_VALID_PROBES["snmp_throughput_above"], threshold_bps="lots")
    with pytest.raises(WatchdogValidationError):
        _validate_probe(bad)
