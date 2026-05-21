"""Unit tests — the config backup / restore service (Hub Tier-2 Feature 3).

`app/services/config_backup.py` — a versioned JSON export of the hub's
operator-managed config, an encrypted-with-secrets variant, and a
dry-run-then-apply import. DB-backed cases use the `hub_db` isolated-
SQLite fixture.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import RuntimeSetting, Scene, Site
from app.models.devices import Device
from app.services import config_backup as svc
from app.services import scenes as scenes_svc
from app.services import sites as sites_svc
from app.services import watchdog as watchdog_svc


def _seed_config(hub_db):
    """Seed a representative operator-managed config."""
    sites_svc.create_site("HQ", "head office")
    scenes_svc.create_scene(
        name="Movie night", description=None,
        items=[{"device_id": "d1", "relay": "off"}],
    )
    watchdog_svc.create_rule(
        name="Internet up",
        probe={"kind": "ping", "host": "1.1.1.1"},
        target={"kind": "tag", "tag": "modems"},
        action={"kind": "notify_only"},
    )
    with session_scope() as s:
        s.add(RuntimeSetting(name="system.portal_name", value={"v": "My Hub"}))
        s.add(RuntimeSetting(name="smtp.password", value={"v": "hunter2"}))
        s.add(Device(
            id="dev_a", mac_address="AA:BB:CC:00:11:22",
            desired_mode="smart_plug",
            desired_config={"device_name": "Lamp"},
        ))


# ── export ─────────────────────────────────────────────────────────────


def test_export_produces_versioned_document(hub_db):
    _seed_config(hub_db)
    doc = json.loads(svc.export_config())
    assert doc["format"] == svc.FORMAT_NAME
    assert doc["format_version"] == svc.FORMAT_VERSION
    assert "sections" in doc
    # Every declared section is present.
    for section in svc.ALL_SECTIONS:
        assert section in doc["sections"]


def test_export_redacts_secrets_by_default(hub_db):
    _seed_config(hub_db)
    doc = json.loads(svc.export_config())
    rs = {r["name"]: r["value"] for r in doc["sections"][svc.SECTION_RUNTIME_SETTINGS]}
    assert rs["smtp.password"] == svc.REDACTED_SENTINEL
    # A non-secret key is exported verbatim.
    assert rs["system.portal_name"] == "My Hub"


def test_export_excludes_operational_data(hub_db):
    _seed_config(hub_db)
    doc = json.loads(svc.export_config())
    # No heartbeats / audit / commands / users sections.
    for forbidden in ("heartbeats", "audit_events", "commands", "users",
                       "device_credentials"):
        assert forbidden not in doc["sections"]


def test_export_device_config_keyed_by_mac(hub_db):
    _seed_config(hub_db)
    doc = json.loads(svc.export_config())
    dev = doc["sections"][svc.SECTION_DEVICE_CONFIG]
    assert len(dev) == 1
    assert dev[0]["mac_address"] == "AA:BB:CC:00:11:22"
    assert dev[0]["desired_config"] == {"device_name": "Lamp"}


def test_export_with_secrets_requires_passphrase(hub_db):
    _seed_config(hub_db)
    with pytest.raises(svc.ConfigBackupError):
        svc.export_config(include_secrets=True, passphrase=None)


def test_encrypted_export_round_trips(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config(include_secrets=True, passphrase="correct horse")
    envelope = json.loads(blob)
    assert envelope["encrypted"] is True
    # The plaintext header stays diff-able even when encrypted.
    assert envelope["format"] == svc.FORMAT_NAME
    decrypted = svc.decrypt_document(envelope, "correct horse")
    rs = {r["name"]: r["value"]
          for r in decrypted["sections"][svc.SECTION_RUNTIME_SETTINGS]}
    # Secrets are intact inside the encrypted document.
    assert rs["smtp.password"] == "hunter2"


def test_encrypted_export_rejects_wrong_passphrase(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config(include_secrets=True, passphrase="right")
    envelope = json.loads(blob)
    with pytest.raises(svc.ConfigBackupError):
        svc.decrypt_document(envelope, "wrong")


# ── import — parse / plan ──────────────────────────────────────────────


def test_parse_rejects_non_backup_file(hub_db):
    with pytest.raises(svc.ConfigBackupError):
        svc.parse_and_plan(json.dumps({"format": "something-else"}).encode())


def test_parse_rejects_bad_json(hub_db):
    with pytest.raises(svc.ConfigBackupError):
        svc.parse_and_plan(b"{not json")


def test_parse_rejects_unsupported_version(hub_db):
    bad = {"format": svc.FORMAT_NAME, "format_version": 999, "sections": {}}
    with pytest.raises(svc.ConfigBackupError):
        svc.parse_and_plan(json.dumps(bad).encode())


def test_plan_marks_new_records_as_create(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config()
    # Wipe everything, then plan the import of the exported file.
    with session_scope() as s:
        for row in s.scalars(select(Site)):
            s.delete(row)
        for row in s.scalars(select(Scene)):
            s.delete(row)
    plan = svc.parse_and_plan(blob)
    sites = next(sp for sp in plan.sections if sp.section == svc.SECTION_SITES)
    assert sites.counts[svc.DISPOSITION_CREATE] == 1


def test_plan_marks_existing_records_as_update(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config()
    # Nothing wiped — every named record already exists.
    plan = svc.parse_and_plan(blob)
    sites = next(sp for sp in plan.sections if sp.section == svc.SECTION_SITES)
    assert sites.counts[svc.DISPOSITION_UPDATE] == 1


def test_plan_skips_network_keys(hub_db):
    with session_scope() as s:
        s.add(RuntimeSetting(name="network.public_base_url",
                             value={"v": "https://old"}))
    blob = svc.export_config()
    plan = svc.parse_and_plan(blob)
    rs = next(sp for sp in plan.sections
              if sp.section == svc.SECTION_RUNTIME_SETTINGS)
    net_items = [it for it in rs.items if it.key.startswith("network.")]
    assert net_items and all(
        it.disposition == svc.DISPOSITION_SKIP for it in net_items
    )
    assert any("network" in w.lower() for w in plan.warnings)


def test_plan_skips_device_config_for_unknown_mac(hub_db):
    doc = {
        "format": svc.FORMAT_NAME,
        "format_version": svc.FORMAT_VERSION,
        "sections": {
            svc.SECTION_DEVICE_CONFIG: [
                {"mac_address": "FF:FF:FF:FF:FF:FF",
                 "desired_config": {"device_name": "X"}, "desired_mode": None},
            ],
        },
    }
    plan = svc.parse_and_plan(json.dumps(doc).encode())
    dc = next(sp for sp in plan.sections
              if sp.section == svc.SECTION_DEVICE_CONFIG)
    assert dc.counts[svc.DISPOSITION_SKIP] == 1


# ── import — round trip (export → wipe → import → parity) ──────────────


def test_full_round_trip_restores_config(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config()

    # Wipe the operator-managed config.
    with session_scope() as s:
        for model in (Site, Scene):
            for row in s.scalars(select(model)):
                s.delete(row)
        for row in s.scalars(select(RuntimeSetting)):
            s.delete(row)
        # Drop the desired_config but keep the device row (a restore
        # re-attaches by MAC to a still-enrolled device).
        dev = s.get(Device, "dev_a")
        dev.desired_config = None
        dev.desired_mode = None

    # Dry-run, then apply.
    plan = svc.parse_and_plan(blob)
    result = svc.apply_plan(plan)

    assert result.to_dict()["total_created"] >= 2  # site + scene at least
    # Parity checks.
    with session_scope() as s:
        assert s.scalar(select(Site).where(Site.name == "HQ")) is not None
        assert s.scalar(select(Scene).where(Scene.name == "Movie night")) is not None
        portal = s.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == "system.portal_name")
        )
        assert portal.value == {"v": "My Hub"}
        dev = s.get(Device, "dev_a")
        assert dev.desired_config == {"device_name": "Lamp"}
        assert dev.desired_mode == "smart_plug"


def test_apply_does_not_write_redacted_secret_value(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config()  # smtp.password redacted
    with session_scope() as s:
        for row in s.scalars(select(RuntimeSetting)):
            s.delete(row)
    plan = svc.parse_and_plan(blob)
    svc.apply_plan(plan)
    with session_scope() as s:
        pw = s.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == "smtp.password")
        )
    # The redacted secret is skipped, never written as the sentinel.
    assert pw is None


def test_encrypted_round_trip_restores_secrets(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config(include_secrets=True, passphrase="pw123")
    with session_scope() as s:
        for row in s.scalars(select(RuntimeSetting)):
            s.delete(row)
    plan = svc.parse_and_plan(blob, passphrase="pw123")
    svc.apply_plan(plan)
    with session_scope() as s:
        pw = s.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == "smtp.password")
        )
    assert pw is not None and pw.value == {"v": "hunter2"}


def test_apply_is_idempotent(hub_db):
    _seed_config(hub_db)
    blob = svc.export_config()
    # First apply — everything already exists, so all updates.
    first = svc.apply_plan(svc.parse_and_plan(blob))
    assert first.to_dict()["total_created"] == 0
    # Second apply — still no creates, no duplicate rows.
    second = svc.apply_plan(svc.parse_and_plan(blob))
    assert second.to_dict()["total_created"] == 0
    with session_scope() as s:
        assert len(list(s.scalars(select(Site)))) == 1
