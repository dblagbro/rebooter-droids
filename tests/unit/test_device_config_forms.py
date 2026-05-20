"""Unit tests — the device desired-config structured-form builders.

`app/blueprints/admin/_device_config_forms.py` maps the device-detail
"Desired config" form's flat `cfg_*` fields to the `desired_config`
dict the service consumes, and back. Pure functions over a `MultiDict`
— no DB, no app context, mirroring `test_rules_forms.py`.

Covered:
  - `build_desired_config_from_form` — form → config, both directions
    of every field group.
  - `desired_config_to_form_values` — config → form pre-population.
  - `is_form_representable` — the round-trip gate that decides whether
    the device-detail page offers the structured form or falls back
    to JSON-only.
"""

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict

from app.blueprints.admin._device_config_forms import (
    DeviceConfigFormError,
    build_desired_config_from_form,
    desired_config_to_form_values,
    is_form_representable,
)


# ── builder: scalar top-level keys ─────────────────────────────────────

def test_empty_form_builds_empty_config():
    assert build_desired_config_from_form(MultiDict()) == {}


def test_device_name_is_carried():
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_device_name", "Erica's Subwoofer")])
    )
    assert cfg == {"device_name": "Erica's Subwoofer"}


def test_device_name_over_120_chars_raises():
    with pytest.raises(DeviceConfigFormError):
        build_desired_config_from_form(
            MultiDict([("cfg_device_name", "x" * 121)])
        )


def test_relay_restore_behavior_select():
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_relay_restore_behavior", "always_on")])
    )
    assert cfg == {"relay_restore_behavior": "always_on"}


def test_relay_restore_behavior_rejects_unknown_value():
    with pytest.raises(DeviceConfigFormError):
        build_desired_config_from_form(
            MultiDict([("cfg_relay_restore_behavior", "explode")])
        )


def test_numeric_scalar_fields_are_ints():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_monitor_interval_seconds", "30"),
        ("cfg_boot_warmup_seconds", "60"),
    ]))
    assert cfg == {"monitor_interval_seconds": 30, "boot_warmup_seconds": 60}


def test_non_numeric_int_field_raises():
    with pytest.raises(DeviceConfigFormError):
        build_desired_config_from_form(
            MultiDict([("cfg_monitor_interval_seconds", "soon")])
        )


def test_blank_int_field_is_omitted():
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_monitor_interval_seconds", "")])
    )
    assert "monitor_interval_seconds" not in cfg


def test_manual_button_checkbox_checked_records_true():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_manual_button_enabled_present", "1"),
        ("cfg_manual_button_enabled", "1"),
    ]))
    assert cfg == {"manual_button_enabled": True}


def test_manual_button_checkbox_unchecked_records_explicit_false():
    # The hidden _present field is posted even when the box is unchecked,
    # so an explicit False is distinguishable from "field absent".
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_manual_button_enabled_present", "1")])
    )
    assert cfg == {"manual_button_enabled": False}


def test_manual_button_absent_present_marker_omits_key():
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_manual_button_enabled", "1")])
    )
    assert "manual_button_enabled" not in cfg


# ── builder: internet watchdog block ───────────────────────────────────

def test_internet_targets_split_one_per_line():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_internet_targets", "1.1.1.1\n8.8.8.8\n"),
        ("cfg_internet_failure_threshold_seconds", "120"),
    ]))
    assert cfg == {
        "internet": {
            "targets": ["1.1.1.1", "8.8.8.8"],
            "failure_threshold_seconds": 120,
        }
    }


def test_internet_block_omitted_when_all_fields_blank():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_internet_targets", "  \n  "),
    ]))
    assert "internet" not in cfg


# ── builder: device watchdog block ─────────────────────────────────────

def test_device_block_carries_target_and_timer():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_device_target", "192.168.1.50"),
        ("cfg_device_cooldown_seconds", "300"),
    ]))
    assert cfg == {
        "device": {"target": "192.168.1.50", "cooldown_seconds": 300}
    }


# ── builder: power block ───────────────────────────────────────────────

def test_power_block_enabled_and_rate():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_power_enabled_present", "1"),
        ("cfg_power_enabled", "1"),
        ("cfg_power_sample_rate_hz", "10"),
    ]))
    assert cfg == {"power": {"enabled": True, "sample_rate_hz": 10}}


# ── builder: notifications block + write-only secret ───────────────────

def test_notifications_block_with_booleans_and_strings():
    cfg = build_desired_config_from_form(MultiDict([
        ("cfg_notifications_enabled_present", "1"),
        ("cfg_notifications_enabled", "1"),
        ("cfg_notifications_webhook_url", "https://example.com/hook"),
    ]))
    assert cfg == {
        "notifications": {
            "enabled": True,
            "webhook_url": "https://example.com/hook",
        }
    }


def test_blank_webhook_token_keeps_existing_secret():
    # A blank password field means "unchanged" — the stored secret is
    # preserved (same trick as the SMTP password field).
    existing = {"notifications": {"webhook_auth_token": "secret-abc"}}
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_notifications_webhook_url", "https://x")]),
        existing=existing,
    )
    assert cfg["notifications"]["webhook_auth_token"] == "secret-abc"


def test_non_blank_webhook_token_replaces_existing_secret():
    existing = {"notifications": {"webhook_auth_token": "old"}}
    cfg = build_desired_config_from_form(
        MultiDict([("cfg_notifications_webhook_auth_token", "new-token")]),
        existing=existing,
    )
    assert cfg["notifications"]["webhook_auth_token"] == "new-token"


# ── inverse: desired_config → form values ──────────────────────────────

def test_to_form_values_flattens_scalars():
    values = desired_config_to_form_values({
        "device_name": "Subwoofer",
        "monitor_interval_seconds": 30,
        "manual_button_enabled": True,
    })
    assert values["cfg_device_name"] == "Subwoofer"
    assert values["cfg_monitor_interval_seconds"] == 30
    assert values["cfg_manual_button_enabled"] is True


def test_to_form_values_flattens_internet_targets_to_lines():
    values = desired_config_to_form_values({
        "internet": {"targets": ["1.1.1.1", "8.8.8.8"]},
    })
    assert values["cfg_internet_targets"] == "1.1.1.1\n8.8.8.8"


def test_to_form_values_never_echoes_webhook_token():
    values = desired_config_to_form_values({
        "notifications": {"webhook_auth_token": "secret", "enabled": True},
    })
    assert "cfg_notifications_webhook_auth_token" not in values
    assert values["cfg_notifications_enabled"] is True


def test_to_form_values_handles_none_and_empty():
    assert desired_config_to_form_values(None) == {}
    assert desired_config_to_form_values({}) == {}


# ── round-trip: builder ↔ inverse ──────────────────────────────────────

def test_round_trip_form_to_config_to_form():
    original_form = MultiDict([
        ("cfg_device_name", "Subwoofer"),
        ("cfg_monitor_interval_seconds", "30"),
        ("cfg_internet_targets", "1.1.1.1\n8.8.8.8"),
        ("cfg_internet_failure_threshold_seconds", "120"),
    ])
    cfg = build_desired_config_from_form(original_form)
    values = desired_config_to_form_values(cfg)
    # Re-feeding the flattened values rebuilds the same config.
    cfg2 = build_desired_config_from_form(MultiDict(values))
    assert cfg2 == cfg


# ── round-trip gate: is_form_representable ─────────────────────────────

def test_empty_config_is_representable():
    assert is_form_representable(None) is True
    assert is_form_representable({}) is True


def test_config_with_only_known_keys_is_representable():
    assert is_form_representable({
        "device_name": "x",
        "internet": {"targets": ["1.1.1.1"], "cooldown_seconds": 30},
        "power": {"enabled": True},
    }) is True


def test_unknown_top_level_key_is_not_representable():
    assert is_form_representable({"wifi_password": "hunter2"}) is False


def test_unknown_subkey_in_object_block_is_not_representable():
    # `internet.exotic_tuning` is not a field the structured form
    # surfaces — editing in the form would silently drop it.
    assert is_form_representable({
        "internet": {"exotic_tuning": 5},
    }) is False


def test_non_dict_object_block_is_not_representable():
    assert is_form_representable({"internet": "not-a-dict"}) is False


def test_internet_targets_as_list_of_objects_is_not_representable():
    # The structured form renders targets one-host-per-line; a list of
    # {host, port} dicts cannot survive that. JSON-only.
    assert is_form_representable({
        "internet": {"targets": [{"host": "1.1.1.1", "port": 53}]},
    }) is False
