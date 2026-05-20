"""Regression tests — S1-4/S1-5 network public-base-URL resolution.

S1-5: `announcements.upsert_announcement` built `central_register_url`
from `load_settings().public_base_url` — env-only — so the
Settings -> Network UI override (`network.public_base_url` in
runtime_settings) was a dead write that never reached devices.

S1-4: a `voipguru.org`-without-`www.` host needs a warn-only check at
startup and on save (never an auto-rewrite).

Fixes:
- `runtime_settings.resolve_public_base_url()` /
  `resolve_firmware_public_base()` — DB override -> env -> config.
- `announcements` now routes through the resolver.
- `voipguru_www_warnings()` — warn-only bare-apex detector.

DB-backed → the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from app.services import runtime_settings as rs
from app.services.announcements import adopt, list_announcements, upsert_announcement


# ── resolver: DB override wins over the config default ──────────────────

def test_resolve_public_base_url_falls_back_to_config(hub_db):
    # No DB override, no env override here → config default.
    assert rs.resolve_public_base_url() == hub_db.public_base_url


def test_resolve_public_base_url_db_override_wins(hub_db):
    rs.set_("network.public_base_url", "https://hub.example.test/rebooter")
    assert rs.resolve_public_base_url() == "https://hub.example.test/rebooter"
    # Clearing the override reverts to the config default.
    rs.delete("network.public_base_url")
    assert rs.resolve_public_base_url() == hub_db.public_base_url


def test_resolve_firmware_public_base_db_override_wins(hub_db):
    rs.set_("network.firmware_public_base", "https://fw.example.test/rebooter/fw")
    assert rs.resolve_firmware_public_base() == "https://fw.example.test/rebooter/fw"


# ── the actual S1-5 bug: announce reflects the DB override ───────────────

def test_central_register_url_uses_db_override(hub_db):
    """The Settings -> Network override must reach the device's
    central_register_url, not just the env-only config default."""
    rs.set_("network.public_base_url", "https://override.example.test/rebooter")

    mac = "AA:BB:CC:5A:5B:5C"
    upsert_announcement(mac_address=mac)
    aid = list_announcements(include_consumed=True)[0]["id"]
    adopt(aid, by_user_id=None)
    resp = upsert_announcement(mac_address=mac)

    assert resp["status"] == "adopted"
    assert resp["central_register_url"] == (
        "https://override.example.test/rebooter/api/v1/device/register"
    )


def test_central_register_url_uses_config_when_no_override(hub_db):
    mac = "AA:BB:CC:6A:6B:6C"
    upsert_announcement(mac_address=mac)
    aid = list_announcements(include_consumed=True)[0]["id"]
    adopt(aid, by_user_id=None)
    resp = upsert_announcement(mac_address=mac)
    assert resp["central_register_url"] == (
        hub_db.public_base_url.rstrip("/") + "/api/v1/device/register"
    )


# ── S1-4: voipguru.org-without-www warn-only check ──────────────────────

def test_www_warning_fires_for_bare_apex_host(hub_db):
    rs.set_("network.public_base_url", "https://voipguru.org/rebooter")
    warnings = rs.voipguru_www_warnings()
    assert any("public_base_url" in w for w in warnings)
    # WARN ONLY — the stored value is never rewritten.
    assert rs.resolve_public_base_url() == "https://voipguru.org/rebooter"


def test_www_warning_silent_for_www_host(hub_db):
    rs.set_("network.public_base_url", "https://www.voipguru.org/rebooter")
    rs.set_("network.firmware_public_base", "https://www.voipguru.org/rebooter/fw")
    assert rs.voipguru_www_warnings() == []


def test_www_warning_silent_for_unrelated_host(hub_db):
    rs.set_("network.public_base_url", "https://hub.example.test/rebooter")
    rs.set_("network.firmware_public_base", "https://hub.example.test/fw")
    assert rs.voipguru_www_warnings() == []


def test_www_warning_fires_for_firmware_base(hub_db):
    rs.set_("network.firmware_public_base", "https://voipguru.org/rebooter/firmware")
    warnings = rs.voipguru_www_warnings()
    assert any("firmware_public_base" in w for w in warnings)
