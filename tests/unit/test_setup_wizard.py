"""Unit tests — Tier-2 Feature 1 setup-wizard rule generation.

`app/services/setup_wizard.py` is the *translator* half of the setup
wizard: it turns plain-language picker answers into a `desired_config`
dict + a watchdog `rule_payload`. The three `apply_*` functions are pure
(no I/O) so most of these tests need no fixture. `find_prior_wizard_rule`
is the one DB-backed helper → it takes the `hub_db` fixture.

The generated `rule_payload` is also fed straight to `watchdog.create_rule`
in the blueprint; a couple of tests assert it survives that real call so
a picker-generated rule can't drift out of sync with the rule engine's
validation.
"""

from __future__ import annotations

import pytest

from app.services import setup_wizard as wiz


# ── mode 1: smart_plug ──────────────────────────────────────────────────

def test_smart_plug_generates_config_and_no_rule():
    result = wiz.apply_smart_plug(
        "dev-1", {"device_name": "Desk lamp", "relay_restore_behavior": "always_on"}
    )
    assert result["desired_mode"] == "smart_plug"
    assert result["rule_payload"] is None
    cfg = result["desired_config"]
    assert cfg["device_name"] == "Desk lamp"
    assert cfg["relay_restore_behavior"] == "always_on"
    # smart-plug mode generates no internet/device watchdog block.
    assert "internet" not in cfg
    assert "device" not in cfg


def test_smart_plug_defaults_relay_restore_to_restore_previous():
    result = wiz.apply_smart_plug("dev-1", {"device_name": "Fan"})
    assert result["desired_config"]["relay_restore_behavior"] == "restore_previous"


def test_smart_plug_rejects_bad_relay_restore_value():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_smart_plug(
            "dev-1", {"device_name": "Fan", "relay_restore_behavior": "explode"}
        )


def test_smart_plug_requires_a_device_name():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_smart_plug("dev-1", {"device_name": "   "})


def test_device_name_length_capped():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_smart_plug("dev-1", {"device_name": "x" * 121})


# ── mode 2: internet_watchdog ───────────────────────────────────────────

def test_internet_watchdog_uses_default_targets_when_blank():
    result = wiz.apply_internet_watchdog("dev-1", {"device_name": "Modem"})
    cfg = result["desired_config"]
    assert cfg["internet"]["targets"] == list(wiz.DEFAULT_INTERNET_TARGETS)
    assert result["rule_payload"]["probe"]["kind"] == "internet"


def test_internet_watchdog_generates_internet_probe_rule_with_cycle():
    result = wiz.apply_internet_watchdog("dev-1", {"device_name": "Modem"})
    rp = result["rule_payload"]
    assert result["desired_mode"] == "internet_watchdog"
    assert rp["probe"]["kind"] == "internet"
    assert rp["target"] == {"kind": "device", "id": "dev-1"}
    assert rp["action"]["kind"] == "cycle"
    # the design requires a marker + predictable name for re-run replace.
    assert rp["description"] == wiz.RULE_MARKER
    assert "dev-1" in rp["name"]


def test_internet_watchdog_parses_custom_targets():
    result = wiz.apply_internet_watchdog(
        "dev-1",
        {"device_name": "Modem", "internet_targets": "9.9.9.9\n  1.0.0.1  \n"},
    )
    targets = result["desired_config"]["internet"]["targets"]
    assert targets == ["9.9.9.9", "1.0.0.1"]
    # the probe carries them as {host,port} objects.
    probe_hosts = [t["host"] for t in result["rule_payload"]["probe"]["targets"]]
    assert probe_hosts == ["9.9.9.9", "1.0.0.1"]


def test_internet_watchdog_accepts_comma_separated_targets():
    result = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem", "internet_targets": "1.1.1.1, 8.8.4.4"}
    )
    assert result["desired_config"]["internet"]["targets"] == ["1.1.1.1", "8.8.4.4"]


def test_internet_watchdog_rejects_too_many_targets():
    # Cap is 10 (firmware supports 10) — 11 must be rejected.
    many = "\n".join(f"10.0.0.{i}" for i in range(11))
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_internet_watchdog(
            "dev-1", {"device_name": "Modem", "internet_targets": many}
        )


def test_internet_watchdog_accepts_ten_targets():
    # Firmware supports 10 internet-mode targets — exactly 10 is allowed.
    ten = "\n".join(f"10.0.0.{i}" for i in range(10))
    result = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem", "internet_targets": ten}
    )
    assert len(result["desired_config"]["internet"]["targets"]) == 10


def test_offline_tolerance_maps_to_failure_threshold():
    # 180 s tolerance / 30 s probe window → threshold 6.
    result = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem", "offline_tolerance_seconds": 180}
    )
    assert result["rule_payload"]["window_seconds"] == wiz.PROBE_WINDOW_SECONDS
    assert result["rule_payload"]["failure_threshold"] == 6


def test_offline_tolerance_rounds_up_and_is_at_least_one():
    # 35 s / 30 s window → ceil = 2.
    result = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem", "offline_tolerance_seconds": 35}
    )
    assert result["rule_payload"]["failure_threshold"] == 2
    # exactly the window → 1 probe.
    result = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem", "offline_tolerance_seconds": 30}
    )
    assert result["rule_payload"]["failure_threshold"] == 1


def test_offline_tolerance_must_be_numeric():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_internet_watchdog(
            "dev-1", {"device_name": "Modem", "offline_tolerance_seconds": "soon"}
        )


def test_finetune_timers_flow_into_config_and_rule():
    result = wiz.apply_internet_watchdog(
        "dev-1",
        {
            "device_name": "Modem",
            "power_off_seconds": 12,
            "cooldown_seconds": 900,
            "max_cycles_per_hour": 4,
        },
    )
    assert result["rule_payload"]["action"]["power_off_seconds"] == 12
    assert result["rule_payload"]["cooldown_seconds"] == 900
    assert result["rule_payload"]["max_cycles_per_hour"] == 4
    block = result["desired_config"]["internet"]
    assert block["power_off_seconds"] == 12
    assert block["cooldown_seconds"] == 900


def test_finetune_timers_default_when_omitted():
    result = wiz.apply_internet_watchdog("dev-1", {"device_name": "Modem"})
    assert (
        result["rule_payload"]["action"]["power_off_seconds"]
        == wiz.DEFAULT_POWER_OFF_SECONDS
    )
    assert (
        result["rule_payload"]["cooldown_seconds"]
        == wiz.DEFAULT_COOLDOWN_SECONDS
    )


# ── mode 3: device_watchdog ─────────────────────────────────────────────

def test_device_watchdog_bare_host_is_a_ping_probe():
    result = wiz.apply_device_watchdog(
        "dev-1", {"device_name": "Camera", "watch_address": "192.168.1.50"}
    )
    assert result["desired_mode"] == "device_watchdog"
    assert result["rule_payload"]["probe"] == {"kind": "ping", "host": "192.168.1.50"}
    assert result["desired_config"]["device"]["target"] == "192.168.1.50"


def test_device_watchdog_host_port_is_a_tcp_probe():
    result = wiz.apply_device_watchdog(
        "dev-1", {"device_name": "NAS", "watch_address": "nas.local:445"}
    )
    assert result["rule_payload"]["probe"] == {
        "kind": "tcp", "host": "nas.local", "port": 445
    }


def test_device_watchdog_url_is_an_http_probe():
    result = wiz.apply_device_watchdog(
        "dev-1",
        {"device_name": "Cam", "watch_address": "https://192.168.1.50/status"},
    )
    assert result["rule_payload"]["probe"] == {
        "kind": "http", "url": "https://192.168.1.50/status"
    }


def test_device_watchdog_rejects_bad_port():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_device_watchdog(
            "dev-1", {"device_name": "NAS", "watch_address": "nas.local:99999"}
        )
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_device_watchdog(
            "dev-1", {"device_name": "NAS", "watch_address": "nas.local:abc"}
        )


def test_device_watchdog_requires_watch_address():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_device_watchdog("dev-1", {"device_name": "Cam"})


def test_device_watchdog_action_is_cycle():
    result = wiz.apply_device_watchdog(
        "dev-1", {"device_name": "Cam", "watch_address": "10.0.0.9"}
    )
    assert result["rule_payload"]["action"]["kind"] == "cycle"
    assert result["rule_payload"]["description"] == wiz.RULE_MARKER


# ── dispatcher ──────────────────────────────────────────────────────────

def test_apply_picker_dispatches_each_mode():
    assert wiz.apply_picker(
        "dev-1", "smart_plug", {"device_name": "L"}
    )["desired_mode"] == "smart_plug"
    assert wiz.apply_picker(
        "dev-1", "internet_watchdog", {"device_name": "M"}
    )["desired_mode"] == "internet_watchdog"
    assert wiz.apply_picker(
        "dev-1", "device_watchdog", {"device_name": "C", "watch_address": "10.0.0.1"}
    )["desired_mode"] == "device_watchdog"


def test_apply_picker_rejects_unknown_mode():
    with pytest.raises(wiz.SetupWizardError):
        wiz.apply_picker("dev-1", "teleport", {"device_name": "L"})


# ── generated rule payloads survive the real create_rule validator ──────

_CREATE_RULE_KEYS = (
    "name", "description", "probe", "target", "action",
    "failure_threshold", "window_seconds", "cooldown_seconds",
)


def _create_from_payload(rule_payload):
    from app.services.watchdog import create_rule

    kwargs = {k: rule_payload[k] for k in _CREATE_RULE_KEYS if k in rule_payload}
    return create_rule(**kwargs)


def test_internet_watchdog_payload_is_valid_for_create_rule(hub_db):
    payload = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem"}
    )["rule_payload"]
    rule = _create_from_payload(payload)
    assert rule["id"].startswith("wdr_")
    assert rule["probe"]["kind"] == "internet"
    assert rule["action"]["kind"] == "cycle"


def test_device_watchdog_payload_is_valid_for_create_rule(hub_db):
    payload = wiz.apply_device_watchdog(
        "dev-1", {"device_name": "Cam", "watch_address": "10.0.0.9:80"}
    )["rule_payload"]
    rule = _create_from_payload(payload)
    assert rule["id"].startswith("wdr_")
    assert rule["probe"]["kind"] == "tcp"


# ── find_prior_wizard_rule (DB-backed) ──────────────────────────────────

def test_find_prior_wizard_rule_none_when_no_rule(hub_db):
    assert wiz.find_prior_wizard_rule("dev-1", "internet_watchdog") is None


def test_find_prior_wizard_rule_matches_marker_and_name(hub_db):
    payload = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem"}
    )["rule_payload"]
    created = _create_from_payload(payload)
    found = wiz.find_prior_wizard_rule("dev-1", "internet_watchdog")
    assert found == created["id"]


def test_find_prior_wizard_rule_ignores_non_wizard_rule(hub_db):
    from app.services.watchdog import create_rule

    # A hand-made rule with the same probe but NOT the wizard marker.
    create_rule(
        name="Setup wizard — internet watchdog for dev-1",
        description="hand-built by an operator",
        probe={"kind": "internet"},
        target={"kind": "device", "id": "dev-1"},
        action={"kind": "cycle"},
    )
    # description doesn't match RULE_MARKER → not treated as a wizard rule.
    assert wiz.find_prior_wizard_rule("dev-1", "internet_watchdog") is None


def test_find_prior_wizard_rule_scoped_per_device(hub_db):
    payload = wiz.apply_internet_watchdog(
        "dev-1", {"device_name": "Modem"}
    )["rule_payload"]
    _create_from_payload(payload)
    # a different device has no wizard rule of its own.
    assert wiz.find_prior_wizard_rule("dev-2", "internet_watchdog") is None
