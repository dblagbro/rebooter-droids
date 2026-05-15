"""Desired-config management — v0.5.22 (B21).

The hub stores the *intended* per-device config in `devices.desired_config`.
On a push (restore-after-reflash, manual "push now", or drift-repair) the
hub enqueues an `apply_config` command carrying that blob. The device's
self-reported config lives in `devices.last_reported_config` once
heartbeats start carrying it.

Today (v0.5.22 first slice):
- `device_name` is the only field the firmware's apply_config v0.1
  schema is documented + tested against. Other top-level keys
  (`internet`, `device`, `notifications`, `power`,
  `relay_restore_behavior`, `monitor_interval_seconds`,
  `boot_warmup_seconds`, `manual_button_enabled`) are accepted as
  pass-through values but firmware-side handling is firmware-team-
  owned and not yet exercised end-to-end.
- Drift detection compares `desired_config` ↔ `last_reported_config`
  field-by-field; mismatches surface in the UI badge + drift-repair
  audit events.

Feature gate: `desired_config.enabled` runtime_setting controls whether
auto-push on restore actually fires. Default OFF through v0.5.22.x —
operator enables once the firmware-side schema for the heavier keys
is validated. Manual push works regardless of the flag.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models import Device

log = logging.getLogger(__name__)


# Top-level keys the operator can set in desired_config. Subset of
# `apply_config v0.1`; matches services/commands.APPLY_CONFIG_ALLOWED_TOP_LEVEL
# so the push path's validation is the same as a manual apply_config.
ALLOWED_DESIRED_CONFIG_KEYS = frozenset({
    "device_name",
    "relay_restore_behavior",
    "monitor_interval_seconds",
    "boot_warmup_seconds",
    "manual_button_enabled",
    "internet",
    "device",
    "notifications",
    "power",
})


class DesiredConfigError(ValueError):
    pass


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def get_desired_config(device_id: str) -> dict | None:
    """Returns the operator-set intended config for the device, or None
    if no desired config has been recorded."""
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        return dict(d.desired_config) if d.desired_config else None


def set_desired_config(
    device_id: str,
    payload: dict,
    *,
    by_user_id: str | None,
    desired_mode: str | None = None,
) -> dict | None:
    """Replace the device's desired_config with `payload`. Validates
    top-level keys against ALLOWED_DESIRED_CONFIG_KEYS. Returns the
    serialized device row, or None if the device doesn't exist.

    No automatic push — the operator decides when via push_desired_config
    or the restore flow.
    """
    if not isinstance(payload, dict):
        raise DesiredConfigError("desired_config must be a JSON object")
    unknown = set(payload.keys()) - ALLOWED_DESIRED_CONFIG_KEYS
    if unknown:
        raise DesiredConfigError(
            f"unsupported keys in desired_config: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_DESIRED_CONFIG_KEYS)}"
        )
    # device_name length cap — DB column is VARCHAR(120) on devices.display_name.
    if "device_name" in payload:
        name = payload.get("device_name")
        if name is not None and not isinstance(name, str):
            raise DesiredConfigError("device_name must be a string")
        if isinstance(name, str) and len(name) > 120:
            raise DesiredConfigError("device_name must be 120 characters or fewer")
    if desired_mode is not None and desired_mode not in (
        "smart_plug",
        "internet_watchdog",
        "device_watchdog",
    ):
        raise DesiredConfigError(
            "desired_mode must be one of "
            "'smart_plug' | 'internet_watchdog' | 'device_watchdog'"
        )

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        d.desired_config = payload or None
        if desired_mode is not None:
            d.desired_mode = desired_mode or None
        d.desired_config_updated_at = now
        d.updated_at = now
        session.add(d)
        session.flush()
        return {
            "device_id": d.id,
            "desired_config": d.desired_config or {},
            "desired_mode": d.desired_mode,
            "desired_config_updated_at": _iso(d.desired_config_updated_at),
        }


def record_reported_config(device_id: str, reported: dict | None) -> None:
    """Called by the heartbeat path when the device echoes its current
    config. Best-effort: never raises out of the heartbeat call site;
    on failure we just log and move on so a malformed payload can't
    block heartbeats.
    """
    if not reported:
        return
    if not isinstance(reported, dict):
        log.warning(
            "record_reported_config: ignoring non-dict payload for %s", device_id
        )
        return
    try:
        with session_scope() as session:
            d = session.get(Device, device_id)
            if d is None:
                return
            d.last_reported_config = reported
            session.add(d)
            session.flush()
    except Exception:
        log.exception("record_reported_config failed for %s", device_id)


def compute_drift(device_id: str) -> dict:
    """Returns a per-field diff between desired_config and
    last_reported_config.

    Shape:
        {"in_sync": bool,
         "missing": [...field names absent from reported...],
         "mismatch": [{"field": ..., "desired": ..., "actual": ...}, ...],
         "extra": [...keys reported but not in desired...]}

    `in_sync` is True only when desired_config is set, last_reported_config
    is set, and every desired field is present + equal in the report.
    """
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return {
                "in_sync": False,
                "missing": [],
                "mismatch": [],
                "extra": [],
                "reason": "device not found",
            }
        desired = d.desired_config or {}
        reported = d.last_reported_config or {}

    if not desired:
        return {
            "in_sync": True,  # no intent → nothing to diff
            "missing": [],
            "mismatch": [],
            "extra": [],
            "reason": "no desired_config set",
        }
    if not reported:
        return {
            "in_sync": False,
            "missing": sorted(desired.keys()),
            "mismatch": [],
            "extra": [],
            "reason": "no last_reported_config recorded yet",
        }

    missing: list[str] = []
    mismatch: list[dict] = []
    for field, want in desired.items():
        if field not in reported:
            missing.append(field)
            continue
        got = reported.get(field)
        if got != want:
            mismatch.append({
                "field": field,
                "desired": want,
                "actual": got,
            })
    extra = sorted(set(reported.keys()) - set(desired.keys()))
    return {
        "in_sync": not (missing or mismatch),
        "missing": sorted(missing),
        "mismatch": mismatch,
        "extra": extra,
    }


PUSH_SOURCES = ("restore", "manual", "drift_repair")


def push_desired_config(
    device_id: str,
    *,
    source: str,
    issued_by_user_id: str | None,
) -> dict:
    """Enqueue an `apply_config` command carrying the device's current
    desired_config. No-op if `desired_config` is empty/NULL.

    Returns {"enqueued": bool, "command_id": str | None, "reason": str | None}.
    """
    if source not in PUSH_SOURCES:
        raise DesiredConfigError(
            f"source must be one of {PUSH_SOURCES}, got {source!r}"
        )
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return {"enqueued": False, "command_id": None, "reason": "device not found"}
        if not d.central_management_enabled:
            return {
                "enqueued": False,
                "command_id": None,
                "reason": "central management disabled",
            }
        if not d.desired_config:
            return {
                "enqueued": False,
                "command_id": None,
                "reason": "no desired_config set",
            }
        payload = dict(d.desired_config)

    # Deferred import — commands.enqueue_for_device transitively pulls
    # in audit + device-lockout helpers that already import from this
    # module's siblings.
    from app.services.commands import enqueue_for_device

    try:
        cmd = enqueue_for_device(
            device_id=device_id,
            cmd_type="apply_config",
            payload=payload,
            issued_by_user_id=issued_by_user_id,
            ttl_seconds=600,
        )
    except Exception as e:
        log.warning(
            "desired-config push failed for %s (source=%s): %s",
            device_id, source, e,
        )
        return {"enqueued": False, "command_id": None, "reason": str(e)}

    # Stamp last_config_pushed_at on success.
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is not None:
            d.last_config_pushed_at = now
            session.add(d)
            session.flush()
    return {
        "enqueued": True,
        "command_id": cmd.id,
        "source": source,
    }


def maybe_push_after_recovery(device_id: str, *, trigger: str) -> dict:
    """v0.5.53 (P0.3 / Phase 4B): recovery-aware desired-config re-push.

    Called from the heartbeat path when a device reports a recovery
    transition — either it just restored last-known-good config, or it
    exited recovery mode. Either way the device's on-box config may have
    diverged from the operator's intent, so the hub re-asserts
    `desired_config`.

    `trigger` is one of `"last_known_good_restored"` | `"recovery_exit"`
    (recorded in the audit detail).

    Gated on the `desired_config.enabled` feature flag exactly like the
    restore-after-reflash auto-push — this is an automatic path, so it
    stays off until the operator opts in. When the flag is off the
    transition is logged (observability) but no command is enqueued.

    Best-effort: never raises out of the heartbeat call site.
    """
    try:
        cfg = get_desired_config(device_id)
        if not cfg:
            return {"pushed": False, "reason": "no desired_config set"}
        if not is_feature_enabled():
            log.info(
                "recovery transition for %s (trigger=%s) — desired_config "
                "push skipped, feature flag off",
                device_id, trigger,
            )
            return {"pushed": False, "reason": "feature disabled"}

        result = push_desired_config(
            device_id, source="restore", issued_by_user_id=None
        )
        if result.get("enqueued"):
            from app.services import audit

            audit.record(
                "device.recovery_config_pushed",
                target_type="device",
                target_id=device_id,
                details={
                    "trigger": trigger,
                    "command_id": result.get("command_id"),
                    "pushed_fields": sorted(cfg.keys()),
                },
            )
            log.info(
                "recovery transition for %s (trigger=%s) — pushed "
                "desired_config, command %s",
                device_id, trigger, result.get("command_id"),
            )
        return {"pushed": bool(result.get("enqueued")), **result}
    except Exception:
        log.exception("maybe_push_after_recovery failed for %s", device_id)
        return {"pushed": False, "reason": "internal error"}


def is_feature_enabled() -> bool:
    """Feature gate. `desired_config.enabled` runtime_setting controls
    whether automatic push paths (restore-after-reflash auto-push,
    drift_repair) actually fire. Manual operator-initiated push always
    fires regardless — the operator has explicit intent then.

    Defaults to False — operator opts in once the firmware-side schema
    for the heavier apply_config keys is validated.
    """
    from app.services import runtime_settings

    val = runtime_settings.get("desired_config.enabled")
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")
