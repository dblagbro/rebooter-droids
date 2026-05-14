"""Action dispatch: cycle / hold_off / notify_only + target resolution.

`resolve_target_devices` is imported by `services/schedule_runtime.py`
too; both names remain accessible at the package root via re-export
from `__init__.py`.

Actions reuse `services/commands.enqueue_for_device` so authz / audit /
TTL behave identically to operator-driven commands. `enqueue_for_device`
is imported lazily inside each fire function to keep this module's
import graph shallow.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db import session_scope
from app.models import WatchdogRule

log = logging.getLogger(__name__)


def _fire_action(rule: WatchdogRule) -> dict:
    """Returns details dict to embed in the action_fired event.

    Exceptions are caught here so the tick keeps moving — the action
    error gets recorded as part of the event instead of bubbling.
    """
    action = rule.action or {}
    target = rule.target or {}
    kind = action.get("kind")
    try:
        if kind == "cycle":
            return _fire_cycle(rule, target, action)
        if kind == "hold_off":
            return _fire_hold_off(rule, target)
        if kind == "notify_only":
            return {"action": "notify_only", "note": "no power action"}
        return {"action": kind, "error": f"unsupported action: {kind}"}
    except Exception as e:
        log.exception("watchdog action failed for rule %s", rule.id)
        return {"action": kind, "error": str(e)}


def _fire_cycle(rule: WatchdogRule, target: dict, action: dict) -> dict:
    from app.services.commands import enqueue_for_device

    device_ids = resolve_target_devices(target)
    if not device_ids:
        return {"action": "cycle", "skipped": "no devices in target"}

    payload = {
        "power_off_seconds": int(action.get("power_off_seconds", 5)),
        "post_reboot_holdoff_seconds": int(action.get("post_reboot_holdoff_seconds", 180)),
    }
    enqueued: list[str] = []
    skipped: list[dict] = []
    for did in device_ids:
        try:
            cmd = enqueue_for_device(
                device_id=did,
                cmd_type="relay_cycle",
                payload=payload,
                issued_by_user_id=None,
                # Watchdog rules treat is_protected as a soft gate — if
                # the operator has marked a device protected, the rule
                # should NOT power-cycle it. enqueue_for_device raises
                # DeviceLockedError; we capture as 'skipped'.
                override_lockout=False,
            )
            enqueued.append(cmd.id)
        except Exception as e:
            skipped.append({"device_id": did, "error": str(e)})
    return {
        "action": "cycle",
        "rule_id": rule.id,
        "enqueued": enqueued,
        "skipped": skipped,
        "payload": payload,
    }


def _fire_hold_off(rule: WatchdogRule, target: dict) -> dict:
    from app.services.commands import enqueue_for_device

    device_ids = resolve_target_devices(target)
    if not device_ids:
        return {"action": "hold_off", "skipped": "no devices in target"}

    held: list[str] = []
    skipped: list[dict] = []
    for did in device_ids:
        try:
            enqueue_for_device(
                device_id=did,
                cmd_type="relay_off",
                payload=None,
                issued_by_user_id=None,
                set_hold_off=True,
            )
            held.append(did)
        except Exception as e:
            skipped.append({"device_id": did, "error": str(e)})
    return {"action": "hold_off", "rule_id": rule.id, "held": held, "skipped": skipped}


def resolve_target_devices(target: dict) -> list[str]:
    """Translate a rule's target spec into the list of device_ids that
    should receive the action this fire.

    kind='device' → single id; kind='group' → all members; kind='tag'
    is currently a no-op (no device_tags primitive yet — tracked as
    B6.3 in BACKLOG)."""
    from app.models import GroupMembership

    kind = target.get("kind")
    with session_scope() as session:
        if kind == "device":
            return [target["id"]] if target.get("id") else []
        if kind == "group":
            gid = target.get("id")
            if not gid:
                return []
            rows = list(
                session.scalars(
                    select(GroupMembership.device_id).where(
                        GroupMembership.group_id == gid
                    )
                )
            )
            return [r for r in rows if r]
        if kind == "tag":
            # Tag-as-target is shaped but no device-tag table ships
            # yet; runtime treats tag targets as no-op until the tag
            # store lands. The rule still records an action_fired
            # event so the operator sees the rule decided to fire —
            # they just need to migrate the rule to a device/group
            # target meanwhile.
            return []
        return []


# v0.5.18 (#3 naming cleanup): the public name is
# `resolve_target_devices`. The underscore alias is kept for one
# release for back-compat with
# `from app.services.watchdog_runtime import _resolve_target_devices`.
# Remove after v0.6.x.
_resolve_target_devices = resolve_target_devices
