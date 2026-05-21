"""Config backup / restore — a versioned, portable JSON export of the
hub's operator-managed configuration.

Feature 3 of the Hub Tier-2 design
(`docs/notes/2026-05-20-hub-tier2-design.md` §3).

What this serialises (operator-managed config — NOT operational data):
  * `runtime_settings` rows (System / Network / SMTP / RBAC) — secrets
    redacted by default.
  * Watchdog rules (minus runtime counters).
  * Schedules (minus runtime counters).
  * Scenes.
  * Sites + groups (names / membership, not device rows).
  * External-sensor sources — secrets redacted.
  * Per-device `desired_config` / `desired_mode`, keyed by MAC.

Explicitly OUT of scope (operational / identity data): heartbeats, power
samples, the audit log, commands, device credentials, enrollment tokens,
users, OAuth identities, sync cursors. Backup is config portability, not
a database dump.

Secrets are redacted to the `__redacted__` sentinel by default. The
operator may instead tick "include secrets (encrypted)", which AES-GCM
encrypts the whole document with a scrypt-derived key from a supplied
passphrase — the passphrase is never logged or audited.

Import is always dry-run-then-confirm: `parse_and_plan()` validates and
builds a per-section `ImportPlan` (create / update / skip / conflict);
`apply_plan()` writes it. The blueprint routes the apply through the
`mass_action` typed-confirmation gate.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models import (
    ExternalSensorSource,
    Group,
    GroupMembership,
    RuntimeSetting,
    Scene,
    Schedule,
    Site,
    WatchdogRule,
)
from app.models.devices import Device

log = logging.getLogger(__name__)

# ── Format constants ───────────────────────────────────────────────────

FORMAT_NAME = "rebooter-hub-config"
FORMAT_VERSION = 1

REDACTED_SENTINEL = "__redacted__"

# Section names — the keys under `sections` in the export document.
SECTION_RUNTIME_SETTINGS = "runtime_settings"
SECTION_WATCHDOG_RULES = "watchdog_rules"
SECTION_SCHEDULES = "schedules"
SECTION_SCENES = "scenes"
SECTION_SITES = "sites"
SECTION_GROUPS = "groups"
SECTION_GROUP_MEMBERSHIPS = "group_memberships"
SECTION_EXTERNAL_SOURCES = "external_sensor_sources"
SECTION_DEVICE_CONFIG = "device_config"

ALL_SECTIONS = (
    SECTION_RUNTIME_SETTINGS,
    SECTION_SITES,
    SECTION_GROUPS,
    SECTION_GROUP_MEMBERSHIPS,
    SECTION_WATCHDOG_RULES,
    SECTION_SCHEDULES,
    SECTION_SCENES,
    SECTION_EXTERNAL_SOURCES,
    SECTION_DEVICE_CONFIG,
)

# Runtime-setting keys whose value is a secret — redacted unless the
# operator opts into an encrypted export. Substring match: any key
# containing one of these tokens is treated as secret.
_SECRET_KEY_TOKENS = ("password", "secret", "token", "api_key", "hmac")

# `runtime_settings.network.*` keys are hub-instance-specific (hostnames,
# CORS origins, cookie domain). Importing them onto a different host
# breaks the hub — the import plan flags them loudly and defaults to skip.
_NETWORK_KEY_PREFIX = "network."

# External-sensor `config` sub-keys that hold a secret.
_EXT_SECRET_SUBKEYS = (
    "token", "api_key", "jwt", "community", "password", "secret",
)


# ── Encryption (AES-GCM with a scrypt-derived key) ─────────────────────


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """scrypt KDF — same parameters the design calls for. 32-byte key
    for AES-256-GCM."""
    return _scrypt(passphrase.encode("utf-8"), salt)


def _scrypt(secret: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1)
    return kdf.derive(secret)


def encrypt_document(doc: dict, passphrase: str) -> bytes:
    """AES-GCM encrypt a backup document.

    Returns a self-describing JSON envelope (so the plaintext header
    stays diff-able) whose `ciphertext` field carries the encrypted
    document. The salt + nonce are stored alongside; the passphrase is
    never persisted.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not passphrase:
        raise ConfigBackupError("a passphrase is required for an encrypted export")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    plaintext = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    envelope = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "encrypted": True,
        "kdf": "scrypt",
        "cipher": "AES-256-GCM",
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "exported_at": _now_iso(),
    }
    return json.dumps(envelope, indent=2).encode("utf-8")


def decrypt_document(envelope: dict, passphrase: str) -> dict:
    """Reverse `encrypt_document`. Raises `ConfigBackupError` on a bad
    passphrase or tampered ciphertext (AES-GCM auth-tag failure)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not passphrase:
        raise ConfigBackupError("this file is encrypted — a passphrase is required")
    try:
        salt = bytes.fromhex(envelope["salt"])
        nonce = bytes.fromhex(envelope["nonce"])
        ciphertext = bytes.fromhex(envelope["ciphertext"])
    except (KeyError, ValueError) as e:
        raise ConfigBackupError(f"malformed encrypted backup envelope: {e}")
    key = _derive_key(passphrase, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise ConfigBackupError(
            "could not decrypt — wrong passphrase or the file was modified"
        )
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ConfigBackupError(f"decrypted payload is not valid JSON: {e}")


# ── Errors ─────────────────────────────────────────────────────────────


class ConfigBackupError(ValueError):
    """A backup export/import operation failed. The message is
    operator-facing."""


# ── Export ─────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_secret_key(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in _SECRET_KEY_TOKENS)


def _redact_ext_config(config: dict | None) -> dict | None:
    """Redact secret sub-keys of an external-sensor `config` blob."""
    if not config:
        return config
    out = dict(config)
    for k in list(out.keys()):
        if any(tok in k.lower() for tok in _EXT_SECRET_SUBKEYS):
            out[k] = REDACTED_SENTINEL
    return out


def export_config(*, include_secrets: bool = False, passphrase: str | None = None):
    """Build the backup document.

    With `include_secrets=False` (the default) secret values are redacted
    to `__redacted__` and a plaintext JSON `bytes` document is returned.

    With `include_secrets=True` a `passphrase` is required: the full
    document (secrets intact) is AES-GCM encrypted and the encrypted
    envelope `bytes` is returned.
    """
    if include_secrets and not passphrase:
        raise ConfigBackupError(
            "including secrets requires a passphrase to encrypt the file"
        )

    doc = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "exported_at": _now_iso(),
        "include_secrets": bool(include_secrets),
        "sections": _collect_sections(include_secrets=include_secrets),
    }

    if include_secrets:
        return encrypt_document(doc, passphrase or "")
    return json.dumps(doc, indent=2).encode("utf-8")


def _collect_sections(*, include_secrets: bool) -> dict[str, list[dict]]:
    """Read every in-scope table into natural-key-addressable records."""
    sections: dict[str, list[dict]] = {}
    with session_scope() as session:
        # runtime_settings — keyed by `name`.
        rs_rows = []
        for r in session.scalars(select(RuntimeSetting).order_by(RuntimeSetting.name)):
            value = (r.value or {}).get("v")
            if not include_secrets and _is_secret_key(r.name):
                value = REDACTED_SENTINEL
            rs_rows.append({"name": r.name, "value": value})
        sections[SECTION_RUNTIME_SETTINGS] = rs_rows

        # sites — keyed by `name`.
        sections[SECTION_SITES] = [
            {"name": s.name, "description": s.description}
            for s in session.scalars(select(Site).order_by(Site.name))
        ]

        # groups — keyed by `name`; site reference is by site name.
        site_name_by_id = {
            s.id: s.name for s in session.scalars(select(Site))
        }
        groups = list(session.scalars(select(Group).order_by(Group.name)))
        sections[SECTION_GROUPS] = [
            {
                "name": g.name,
                "description": g.description,
                "site_name": site_name_by_id.get(g.site_id),
            }
            for g in groups
        ]

        # group memberships — keyed by (group name, device MAC). Devices
        # are referenced by MAC so a restore re-attaches to re-enrolled
        # hardware. Memberships for devices with no MAC are dropped.
        group_name_by_id = {g.id: g.name for g in groups}
        device_mac_by_id = {
            d.id: d.mac_address
            for d in session.scalars(select(Device))
        }
        memberships = []
        for m in session.scalars(select(GroupMembership)):
            gname = group_name_by_id.get(m.group_id)
            mac = device_mac_by_id.get(m.device_id)
            if gname and mac:
                memberships.append({"group_name": gname, "device_mac": mac})
        sections[SECTION_GROUP_MEMBERSHIPS] = memberships

        # watchdog rules — keyed by `name`; runtime counters dropped.
        sections[SECTION_WATCHDOG_RULES] = [
            {
                "name": r.name,
                "description": r.description,
                "enabled": bool(r.enabled),
                "probe": r.probe or {},
                "target": r.target or {},
                "action": r.action or {},
                "escalation": r.escalation or {},
                "maintenance_windows": r.maintenance_windows or [],
                "failure_threshold": r.failure_threshold,
                "recovery_threshold": r.recovery_threshold,
                "window_seconds": r.window_seconds,
                "cooldown_seconds": r.cooldown_seconds,
                "max_retries": r.max_retries,
                "retry_delay_seconds": r.retry_delay_seconds,
                "site_name": site_name_by_id.get(r.site_id),
            }
            for r in session.scalars(select(WatchdogRule).order_by(WatchdogRule.name))
        ]

        # schedules — keyed by `name`; runtime counters dropped.
        sections[SECTION_SCHEDULES] = [
            {
                "name": s.name,
                "description": s.description,
                "enabled": bool(s.enabled),
                "kind": s.kind,
                "recurrence": s.recurrence,
                "at_time_utc": s.at_time_utc,
                "weekdays": s.weekdays or [],
                "duration_seconds": s.duration_seconds,
                "target": s.target or {},
                "power_off_seconds": s.power_off_seconds,
                "post_reboot_holdoff_seconds": s.post_reboot_holdoff_seconds,
            }
            for s in session.scalars(select(Schedule).order_by(Schedule.name))
        ]

        # scenes — keyed by `name`.
        sections[SECTION_SCENES] = [
            {
                "name": s.name,
                "description": s.description,
                "items": s.items or [],
            }
            for s in session.scalars(select(Scene).order_by(Scene.name))
        ]

        # external sensor sources — keyed by `display_name`; config
        # secrets redacted unless an encrypted export.
        ext_rows = []
        for e in session.scalars(
            select(ExternalSensorSource).order_by(ExternalSensorSource.display_name)
        ):
            cfg = e.config
            if not include_secrets:
                cfg = _redact_ext_config(cfg)
            ext_rows.append({
                "display_name": e.display_name,
                "kind": e.kind,
                "host": e.host,
                "port": e.port,
                "enabled": bool(e.enabled),
                "poll_interval_seconds": e.poll_interval_seconds,
                "config": cfg,
            })
        sections[SECTION_EXTERNAL_SOURCES] = ext_rows

        # per-device desired_config — keyed by MAC. Devices with no MAC
        # or no desired_config are skipped (nothing portable to restore).
        dev_rows = []
        for d in session.scalars(select(Device).order_by(Device.mac_address)):
            if not d.mac_address:
                continue
            if d.desired_config is None and d.desired_mode is None:
                continue
            dev_rows.append({
                "mac_address": d.mac_address,
                "desired_config": d.desired_config,
                "desired_mode": d.desired_mode,
            })
        sections[SECTION_DEVICE_CONFIG] = dev_rows

    return sections


# ── Import — dry-run plan ──────────────────────────────────────────────

# Per-record dispositions.
DISPOSITION_CREATE = "create"
DISPOSITION_UPDATE = "update"
DISPOSITION_SKIP = "skip"
DISPOSITION_CONFLICT = "conflict"


@dataclass
class PlanItem:
    """One record's planned disposition within a section."""

    key: str
    disposition: str
    note: str = ""
    record: dict = field(default_factory=dict)


@dataclass
class SectionPlan:
    """The dry-run plan for one section."""

    section: str
    items: list[PlanItem] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {
            DISPOSITION_CREATE: 0,
            DISPOSITION_UPDATE: 0,
            DISPOSITION_SKIP: 0,
            DISPOSITION_CONFLICT: 0,
        }
        for it in self.items:
            out[it.disposition] = out.get(it.disposition, 0) + 1
        return out


@dataclass
class ImportPlan:
    """The full dry-run plan across all sections."""

    sections: list[SectionPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    document: dict = field(default_factory=dict)

    @property
    def total_writes(self) -> int:
        """Records that would be created or updated — the mass-action
        blast radius."""
        n = 0
        for sp in self.sections:
            c = sp.counts
            n += c[DISPOSITION_CREATE] + c[DISPOSITION_UPDATE]
        return n

    def to_dict(self) -> dict:
        """JSON-safe shape for stashing in the session + template
        rendering."""
        return {
            "warnings": list(self.warnings),
            "document": self.document,
            "sections": [
                {
                    "section": sp.section,
                    "counts": sp.counts,
                    "items": [
                        {
                            "key": it.key,
                            "disposition": it.disposition,
                            "note": it.note,
                        }
                        for it in sp.items
                    ],
                }
                for sp in self.sections
            ],
            "total_writes": self.total_writes,
        }


def parse_and_plan(file_bytes: bytes, passphrase: str | None = None) -> ImportPlan:
    """Parse an uploaded backup file and build a dry-run `ImportPlan`.

    Validates the format header and `format_version`, decrypts if the
    file is an encrypted envelope, then diffs every section's records
    against what is already on this hub. Writes nothing.
    """
    try:
        raw = json.loads((file_bytes or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ConfigBackupError(f"file is not valid JSON: {e}")
    if not isinstance(raw, dict):
        raise ConfigBackupError("backup file must be a JSON object")

    if raw.get("format") != FORMAT_NAME:
        raise ConfigBackupError(
            f"not a Rebooter hub config backup (expected format "
            f"{FORMAT_NAME!r})"
        )

    if raw.get("encrypted"):
        doc = decrypt_document(raw, passphrase or "")
    else:
        doc = raw

    version = doc.get("format_version")
    if version != FORMAT_VERSION:
        raise ConfigBackupError(
            f"unsupported backup format_version {version!r} "
            f"(this hub reads version {FORMAT_VERSION})"
        )

    sections = doc.get("sections")
    if not isinstance(sections, dict):
        raise ConfigBackupError("backup document has no `sections` object")

    plan = ImportPlan(document=doc)
    with session_scope() as session:
        _plan_runtime_settings(plan, session, sections.get(SECTION_RUNTIME_SETTINGS, []))
        _plan_named(plan, session, SECTION_SITES, Site, sections.get(SECTION_SITES, []))
        _plan_named(plan, session, SECTION_GROUPS, Group, sections.get(SECTION_GROUPS, []))
        _plan_group_memberships(plan, session, sections.get(SECTION_GROUP_MEMBERSHIPS, []))
        _plan_named(plan, session, SECTION_WATCHDOG_RULES, WatchdogRule,
                    sections.get(SECTION_WATCHDOG_RULES, []))
        _plan_named(plan, session, SECTION_SCHEDULES, Schedule,
                    sections.get(SECTION_SCHEDULES, []))
        _plan_named(plan, session, SECTION_SCENES, Scene, sections.get(SECTION_SCENES, []))
        _plan_external_sources(plan, session, sections.get(SECTION_EXTERNAL_SOURCES, []))
        _plan_device_config(plan, session, sections.get(SECTION_DEVICE_CONFIG, []))

    return plan


def _plan_runtime_settings(plan: ImportPlan, session, records: list) -> None:
    sp = SectionPlan(section=SECTION_RUNTIME_SETTINGS)
    existing = {r.name: r for r in session.scalars(select(RuntimeSetting))}
    for rec in records or []:
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        value = rec.get("value")
        if value == REDACTED_SENTINEL:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_SKIP,
                note="redacted secret — not importable", record=rec,
            ))
            continue
        if name.startswith(_NETWORK_KEY_PREFIX):
            # Hub-instance-specific — importing onto a different host
            # breaks the hub. Default to skip and flag loudly.
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_SKIP,
                note="network key — host-specific, skipped to protect this hub",
                record=rec,
            ))
            continue
        if name in existing:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_UPDATE,
                note="overwrites the current value", record=rec,
            ))
        else:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_CREATE, record=rec,
            ))
    if any(it.note.startswith("network key") for it in sp.items):
        plan.warnings.append(
            "Network settings (network.*) were found in the backup and "
            "default to SKIP — importing them onto a different hostname "
            "would break this hub."
        )
    plan.sections.append(sp)


def _plan_named(plan: ImportPlan, session, section: str, model, records: list) -> None:
    """Plan a section keyed by the model's unique `name` column.

    Last-writer-wins: an existing row with the same name is an UPDATE; a
    new name is a CREATE.
    """
    sp = SectionPlan(section=section)
    # `select(model.name)` yields the scalar name values directly.
    existing = set(session.scalars(select(model.name)))
    seen: set[str] = set()
    for rec in records or []:
        name = (rec.get("name") or "").strip()
        if not name:
            sp.items.append(PlanItem(
                key="(unnamed)", disposition=DISPOSITION_SKIP,
                note="record has no name", record=rec,
            ))
            continue
        if name in seen:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_CONFLICT,
                note="duplicate name within the backup file", record=rec,
            ))
            continue
        seen.add(name)
        if name in existing:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_UPDATE,
                note="updates the existing record", record=rec,
            ))
        else:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_CREATE, record=rec,
            ))
    plan.sections.append(sp)


def _plan_group_memberships(plan: ImportPlan, session, records: list) -> None:
    sp = SectionPlan(section=SECTION_GROUP_MEMBERSHIPS)
    group_by_name = {g.name: g for g in session.scalars(select(Group))}
    device_by_mac = {
        d.mac_address: d
        for d in session.scalars(select(Device))
        if d.mac_address
    }
    existing_pairs = {
        (m.group_id, m.device_id)
        for m in session.scalars(select(GroupMembership))
    }
    for rec in records or []:
        gname = (rec.get("group_name") or "").strip()
        mac = (rec.get("device_mac") or "").strip()
        key = f"{gname} / {mac}"
        device = device_by_mac.get(mac)
        if device is None:
            sp.items.append(PlanItem(
                key=key, disposition=DISPOSITION_SKIP,
                note="device not enrolled here (MAC not found)", record=rec,
            ))
            continue
        group = group_by_name.get(gname)
        # If the group will be created in this same import its row isn't
        # in `existing` yet — still a valid CREATE for the membership.
        if group is not None and (group.id, device.id) in existing_pairs:
            sp.items.append(PlanItem(
                key=key, disposition=DISPOSITION_SKIP,
                note="membership already exists", record=rec,
            ))
        else:
            sp.items.append(PlanItem(
                key=key, disposition=DISPOSITION_CREATE, record=rec,
            ))
    plan.sections.append(sp)


def _plan_external_sources(plan: ImportPlan, session, records: list) -> None:
    sp = SectionPlan(section=SECTION_EXTERNAL_SOURCES)
    existing = {
        e.display_name for e in session.scalars(select(ExternalSensorSource))
    }
    seen: set[str] = set()
    for rec in records or []:
        name = (rec.get("display_name") or "").strip()
        if not name:
            sp.items.append(PlanItem(
                key="(unnamed)", disposition=DISPOSITION_SKIP,
                note="record has no display_name", record=rec,
            ))
            continue
        if name in seen:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_CONFLICT,
                note="duplicate display_name within the backup", record=rec,
            ))
            continue
        seen.add(name)
        note = ""
        cfg = rec.get("config") or {}
        if any(v == REDACTED_SENTINEL for v in cfg.values()):
            note = "config has redacted secrets — re-enter them after import"
        if name in existing:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_UPDATE,
                note=note or "updates the existing source", record=rec,
            ))
        else:
            sp.items.append(PlanItem(
                key=name, disposition=DISPOSITION_CREATE, note=note, record=rec,
            ))
    plan.sections.append(sp)


def _plan_device_config(plan: ImportPlan, session, records: list) -> None:
    sp = SectionPlan(section=SECTION_DEVICE_CONFIG)
    device_by_mac = {
        d.mac_address: d
        for d in session.scalars(select(Device))
        if d.mac_address
    }
    for rec in records or []:
        mac = (rec.get("mac_address") or "").strip()
        if not mac:
            sp.items.append(PlanItem(
                key="(no mac)", disposition=DISPOSITION_SKIP,
                note="record has no mac_address", record=rec,
            ))
            continue
        if mac not in device_by_mac:
            sp.items.append(PlanItem(
                key=mac, disposition=DISPOSITION_SKIP,
                note="device not enrolled here", record=rec,
            ))
            continue
        sp.items.append(PlanItem(
            key=mac, disposition=DISPOSITION_UPDATE,
            note="re-attaches desired_config to this device", record=rec,
        ))
    plan.sections.append(sp)


# ── Import — apply ─────────────────────────────────────────────────────


@dataclass
class ImportResult:
    """Per-section applied counts."""

    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "created": dict(self.created),
            "updated": dict(self.updated),
            "skipped": dict(self.skipped),
            "total_created": sum(self.created.values()),
            "total_updated": sum(self.updated.values()),
            "total_skipped": sum(self.skipped.values()),
        }


def apply_plan(plan: ImportPlan) -> ImportResult:
    """Apply a previously-built `ImportPlan`.

    Only `create` / `update` items are written; `skip` / `conflict`
    items are counted and left alone. The whole import runs inside one
    `session_scope()` transaction so a mid-import failure rolls back
    cleanly.
    """
    result = ImportResult()
    by_section = {sp.section: sp for sp in plan.sections}

    with session_scope() as session:
        # Order matters — sites before groups, groups before memberships
        # and rules so name → row references resolve.
        _apply_runtime_settings(session, by_section.get(SECTION_RUNTIME_SETTINGS), result)
        _apply_sites(session, by_section.get(SECTION_SITES), result)
        _apply_groups(session, by_section.get(SECTION_GROUPS), result)
        session.flush()
        _apply_group_memberships(session, by_section.get(SECTION_GROUP_MEMBERSHIPS), result)
        _apply_watchdog_rules(session, by_section.get(SECTION_WATCHDOG_RULES), result)
        _apply_schedules(session, by_section.get(SECTION_SCHEDULES), result)
        _apply_scenes(session, by_section.get(SECTION_SCENES), result)
        _apply_external_sources(session, by_section.get(SECTION_EXTERNAL_SOURCES), result)
        _apply_device_config(session, by_section.get(SECTION_DEVICE_CONFIG), result)

    return result


def _bump(d: dict[str, int], section: str) -> None:
    d[section] = d.get(section, 0) + 1


def _writeable(sp: SectionPlan | None):
    """Yield (item) for every create/update item in a section plan."""
    if sp is None:
        return
    for it in sp.items:
        if it.disposition in (DISPOSITION_CREATE, DISPOSITION_UPDATE):
            yield it


def _count_skips(sp: SectionPlan | None, result: ImportResult) -> None:
    if sp is None:
        return
    for it in sp.items:
        if it.disposition in (DISPOSITION_SKIP, DISPOSITION_CONFLICT):
            _bump(result.skipped, sp.section)


def _apply_runtime_settings(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    existing = {r.name: r for r in session.scalars(select(RuntimeSetting))}
    for it in _writeable(sp):
        rec = it.record
        name = rec["name"]
        row = existing.get(name)
        if row is None:
            row = RuntimeSetting(name=name, value={"v": rec.get("value")})
            session.add(row)
            _bump(result.created, SECTION_RUNTIME_SETTINGS)
        else:
            row.value = {"v": rec.get("value")}
            _bump(result.updated, SECTION_RUNTIME_SETTINGS)
        row.updated_at = datetime.now(timezone.utc)


def _apply_sites(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    existing = {s.name: s for s in session.scalars(select(Site))}
    for it in _writeable(sp):
        rec = it.record
        row = existing.get(rec["name"])
        if row is None:
            row = Site(name=rec["name"], description=rec.get("description"))
            session.add(row)
            _bump(result.created, SECTION_SITES)
        else:
            row.description = rec.get("description")
            _bump(result.updated, SECTION_SITES)


def _apply_groups(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    site_by_name = {s.name: s for s in session.scalars(select(Site))}
    existing = {g.name: g for g in session.scalars(select(Group))}
    for it in _writeable(sp):
        rec = it.record
        site = site_by_name.get(rec.get("site_name"))
        row = existing.get(rec["name"])
        if row is None:
            row = Group(
                name=rec["name"],
                description=rec.get("description"),
                site_id=site.id if site else None,
            )
            session.add(row)
            _bump(result.created, SECTION_GROUPS)
        else:
            row.description = rec.get("description")
            row.site_id = site.id if site else None
            _bump(result.updated, SECTION_GROUPS)


def _apply_group_memberships(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    group_by_name = {g.name: g for g in session.scalars(select(Group))}
    device_by_mac = {
        d.mac_address: d
        for d in session.scalars(select(Device))
        if d.mac_address
    }
    existing_pairs = {
        (m.group_id, m.device_id)
        for m in session.scalars(select(GroupMembership))
    }
    for it in _writeable(sp):
        rec = it.record
        group = group_by_name.get(rec.get("group_name"))
        device = device_by_mac.get(rec.get("device_mac"))
        if group is None or device is None:
            _bump(result.skipped, SECTION_GROUP_MEMBERSHIPS)
            continue
        if (group.id, device.id) in existing_pairs:
            _bump(result.skipped, SECTION_GROUP_MEMBERSHIPS)
            continue
        session.add(GroupMembership(group_id=group.id, device_id=device.id))
        existing_pairs.add((group.id, device.id))
        _bump(result.created, SECTION_GROUP_MEMBERSHIPS)


def _apply_watchdog_rules(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    site_by_name = {s.name: s for s in session.scalars(select(Site))}
    existing = {r.name: r for r in session.scalars(select(WatchdogRule))}
    for it in _writeable(sp):
        rec = it.record
        site = site_by_name.get(rec.get("site_name"))
        fields = dict(
            description=rec.get("description"),
            enabled=bool(rec.get("enabled", True)),
            probe=rec.get("probe") or {},
            target=rec.get("target") or {},
            action=rec.get("action") or {},
            escalation=rec.get("escalation") or {"kind": "stop"},
            maintenance_windows=rec.get("maintenance_windows") or [],
            failure_threshold=int(rec.get("failure_threshold", 3)),
            recovery_threshold=int(rec.get("recovery_threshold", 2)),
            window_seconds=int(rec.get("window_seconds", 60)),
            cooldown_seconds=int(rec.get("cooldown_seconds", 300)),
            max_retries=int(rec.get("max_retries", 3)),
            retry_delay_seconds=int(rec.get("retry_delay_seconds", 60)),
            site_id=site.id if site else None,
        )
        row = existing.get(rec["name"])
        if row is None:
            session.add(WatchdogRule(name=rec["name"], **fields))
            _bump(result.created, SECTION_WATCHDOG_RULES)
        else:
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = datetime.now(timezone.utc)
            _bump(result.updated, SECTION_WATCHDOG_RULES)


def _apply_schedules(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    existing = {s.name: s for s in session.scalars(select(Schedule))}
    for it in _writeable(sp):
        rec = it.record
        fields = dict(
            description=rec.get("description"),
            enabled=bool(rec.get("enabled", True)),
            kind=rec.get("kind"),
            recurrence=rec.get("recurrence"),
            at_time_utc=rec.get("at_time_utc"),
            weekdays=rec.get("weekdays") or [],
            duration_seconds=int(rec.get("duration_seconds", 0)),
            target=rec.get("target") or {},
            power_off_seconds=int(rec.get("power_off_seconds", 5)),
            post_reboot_holdoff_seconds=int(rec.get("post_reboot_holdoff_seconds", 180)),
        )
        row = existing.get(rec["name"])
        if row is None:
            session.add(Schedule(name=rec["name"], **fields))
            _bump(result.created, SECTION_SCHEDULES)
        else:
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = datetime.now(timezone.utc)
            _bump(result.updated, SECTION_SCHEDULES)


def _apply_scenes(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    existing = {s.name: s for s in session.scalars(select(Scene))}
    for it in _writeable(sp):
        rec = it.record
        row = existing.get(rec["name"])
        if row is None:
            session.add(Scene(
                name=rec["name"],
                description=rec.get("description"),
                items=rec.get("items") or [],
            ))
            _bump(result.created, SECTION_SCENES)
        else:
            row.description = rec.get("description")
            row.items = rec.get("items") or []
            row.updated_at = datetime.now(timezone.utc)
            _bump(result.updated, SECTION_SCENES)


def _apply_external_sources(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    existing = {
        e.display_name: e for e in session.scalars(select(ExternalSensorSource))
    }
    for it in _writeable(sp):
        rec = it.record
        cfg = rec.get("config") or {}
        # Drop redacted placeholders so an import never writes the
        # literal sentinel string into a live secret field.
        cfg = {k: v for k, v in cfg.items() if v != REDACTED_SENTINEL}
        fields = dict(
            kind=rec.get("kind"),
            host=rec.get("host") or "",
            port=int(rec.get("port", 8060)),
            enabled=bool(rec.get("enabled", True)),
            poll_interval_seconds=int(rec.get("poll_interval_seconds", 30)),
        )
        row = existing.get(rec["display_name"])
        if row is None:
            session.add(ExternalSensorSource(
                display_name=rec["display_name"], config=cfg or None, **fields,
            ))
            _bump(result.created, SECTION_EXTERNAL_SOURCES)
        else:
            for k, v in fields.items():
                setattr(row, k, v)
            # Merge config: keep existing secrets, overlay imported
            # non-secret keys.
            merged = dict(row.config or {})
            merged.update(cfg)
            row.config = merged or None
            row.updated_at = datetime.now(timezone.utc)
            _bump(result.updated, SECTION_EXTERNAL_SOURCES)


def _apply_device_config(session, sp: SectionPlan | None, result: ImportResult) -> None:
    _count_skips(sp, result)
    device_by_mac = {
        d.mac_address: d
        for d in session.scalars(select(Device))
        if d.mac_address
    }
    for it in _writeable(sp):
        rec = it.record
        device = device_by_mac.get(rec.get("mac_address"))
        if device is None:
            _bump(result.skipped, SECTION_DEVICE_CONFIG)
            continue
        device.desired_config = rec.get("desired_config")
        device.desired_mode = rec.get("desired_mode")
        device.desired_config_updated_at = datetime.now(timezone.utc)
        _bump(result.updated, SECTION_DEVICE_CONFIG)
