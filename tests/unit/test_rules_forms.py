"""Unit tests — the rule-create form → JSON-shape builders.

`app/blueprints/admin/_rules_forms.py` maps the rules form's flat
fields to the probe / target / action / maintenance-window JSON shapes.
Pure functions over a `MultiDict` — no DB, no app context. The
structured rule-edit form (#15) reuses these, so they carry real
weight.
"""

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict

from app.blueprints.admin._rules_forms import (
    RuleFormError,
    build_action_from_form,
    build_maintenance_windows_from_form,
    build_probe_from_form,
    build_target_from_form,
)


# ── probe ──────────────────────────────────────────────────────────────

def test_probe_ping():
    probe = build_probe_from_form(
        MultiDict([("probe_kind", "ping"), ("probe_arg", "192.168.1.1")])
    )
    assert probe == {"kind": "ping", "host": "192.168.1.1"}


def test_probe_tcp_splits_host_and_port():
    probe = build_probe_from_form(
        MultiDict([("probe_kind", "tcp"), ("probe_arg", "10.0.0.1:22")])
    )
    assert probe == {"kind": "tcp", "host": "10.0.0.1", "port": 22}


def test_probe_http():
    probe = build_probe_from_form(
        MultiDict([("probe_kind", "http"), ("probe_arg", "https://example.com")])
    )
    assert probe == {"kind": "http", "url": "https://example.com"}


def test_probe_gateway_has_no_arg():
    assert build_probe_from_form(MultiDict([("probe_kind", "gateway")])) == {
        "kind": "gateway"
    }


def test_probe_internet_zips_target_rows_and_drops_empties():
    probe = build_probe_from_form(MultiDict([
        ("probe_kind", "internet"),
        ("internet_target_host[]", "1.1.1.1"),
        ("internet_target_port[]", "53"),
        ("internet_target_host[]", "8.8.8.8"),
        ("internet_target_port[]", "53"),
        # a trailing blank row the UI keeps for "add another"
        ("internet_target_host[]", ""),
        ("internet_target_port[]", ""),
    ]))
    assert probe == {
        "kind": "internet",
        "targets": [
            {"host": "1.1.1.1", "port": 53},
            {"host": "8.8.8.8", "port": 53},
        ],
    }


def test_probe_roku_carries_per_kind_fields():
    probe = build_probe_from_form(MultiDict([
        ("probe_kind", "roku_app_active"),
        ("roku_source_id", "ext_abc"),
        ("roku_app_name", "Spectrum TV"),
        ("roku_max_sample_age_seconds", "90"),
    ]))
    assert probe["kind"] == "roku_app_active"
    assert probe["source_id"] == "ext_abc"
    assert probe["app_name"] == "Spectrum TV"
    assert probe["max_sample_age_seconds"] == 90


def test_probe_unknown_kind_raises():
    with pytest.raises(RuleFormError):
        build_probe_from_form(MultiDict([("probe_kind", "bogus")]))


def test_probe_power_above_non_numeric_threshold_raises():
    with pytest.raises(RuleFormError):
        build_probe_from_form(MultiDict([
            ("probe_kind", "power_above"),
            ("power_device_id", "dev_x"),
            ("power_threshold_w", "not-a-number"),
        ]))


# ── target ─────────────────────────────────────────────────────────────

def test_target_device():
    assert build_target_from_form(
        MultiDict([("target_kind", "device"), ("target_id", "dev_x")])
    ) == {"kind": "device", "id": "dev_x"}


def test_target_tag_uses_tag_key():
    assert build_target_from_form(
        MultiDict([("target_kind", "tag"), ("target_id", "edge")])
    ) == {"kind": "tag", "tag": "edge"}


def test_target_unknown_kind_raises():
    with pytest.raises(RuleFormError):
        build_target_from_form(MultiDict([("target_kind", "")]))


# ── action ─────────────────────────────────────────────────────────────

def test_action_cycle_defaults():
    action = build_action_from_form(MultiDict([("action_kind", "cycle")]))
    assert action == {
        "kind": "cycle",
        "power_off_seconds": 5,
        "post_reboot_holdoff_seconds": 180,
    }


def test_action_hold_off():
    assert build_action_from_form(
        MultiDict([("action_kind", "hold_off")])
    ) == {"kind": "hold_off"}


def test_action_unknown_kind_raises():
    with pytest.raises(RuleFormError):
        build_action_from_form(MultiDict([("action_kind", "explode")]))


# ── maintenance windows ────────────────────────────────────────────────

def test_maintenance_window_tags_naive_datetime_local_as_utc():
    windows = build_maintenance_windows_from_form(MultiDict([
        ("maint_start", "2026-01-01T02:00"),
        ("maint_end", "2026-01-01T03:00"),
    ]))
    assert windows == [
        {"start": "2026-01-01T02:00:00+00:00", "end": "2026-01-01T03:00:00+00:00"},
    ]


def test_maintenance_window_none_when_unset():
    assert build_maintenance_windows_from_form(MultiDict()) is None
