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
    try:
        return _dispatch_one(rule, rule.action or {}, rule.target or {})
    except Exception as e:
        log.exception("watchdog action failed for rule %s", rule.id)
        return {"action": (rule.action or {}).get("kind"), "error": str(e)}


def _dispatch_one(rule: WatchdogRule, action: dict, target: dict) -> dict:
    """Apply one leaf action against `target`. Raises on failure —
    callers wrap this. A `binding` meta-action never reaches here; the
    binding runtime resolves it to a leaf and calls back in."""
    kind = (action or {}).get("kind")
    if kind == "cycle":
        return _fire_cycle(rule, target, action)
    if kind == "hold_off":
        return _fire_hold_off(rule, target)
    if kind == "relay_on":
        return _fire_relay_set(rule, target, "relay_on")
    if kind == "relay_off":
        return _fire_relay_set(rule, target, "relay_off")
    if kind == "apply_scene":
        return _fire_scene(rule, action)
    if kind == "notify_only":
        return {"action": "notify_only", "note": "no power action"}
    return {"action": kind, "error": f"unsupported action: {kind}"}


def apply_binding_action(rule: WatchdogRule, action: dict) -> dict:
    """v0.5.90 (Stage A): apply one binding edge — the `on_active` or
    `on_clear` leaf action chosen by `_binding_tick`. Same
    exception-capture contract as `_fire_action` so the tick keeps
    moving on an action error."""
    try:
        return _dispatch_one(rule, action or {}, rule.target or {})
    except Exception as e:
        log.exception("watchdog binding action failed for rule %s", rule.id)
        return {"action": (action or {}).get("kind"), "error": str(e)}


def _fire_relay_set(rule: WatchdogRule, target: dict, cmd_type: str) -> dict:
    """v0.5.90 (Stage A): idempotent set-state action — enqueue a plain
    `relay_on` / `relay_off` for every target device. Unlike `hold_off`
    this does NOT set the sticky `is_held_off` flag: a binding rule
    owns its target's state and must stay free to flip it back."""
    from app.services.commands import enqueue_for_device

    device_ids = resolve_target_devices(target)
    if not device_ids:
        return {"action": cmd_type, "skipped": "no devices in target"}

    enqueued: list[str] = []
    skipped: list[dict] = []
    for did in device_ids:
        try:
            cmd = enqueue_for_device(
                device_id=did,
                cmd_type=cmd_type,
                payload=None,
                issued_by_user_id=None,
                # Same soft is_protected gate as _fire_cycle.
                override_lockout=False,
            )
            enqueued.append(cmd.id)
        except Exception as e:
            skipped.append({"device_id": did, "error": str(e)})
    return {
        "action": cmd_type,
        "rule_id": rule.id,
        "enqueued": enqueued,
        "skipped": skipped,
    }


def _fire_scene(rule: WatchdogRule, action: dict) -> dict:
    """v0.5.91 (Stage B): apply a multi-device scene. Each `items`
    entry sets one named device to a relay state and/or pushes an
    `apply_config` payload — so a single action can put the surround
    AND subwoofer into the state Erica wants while Jeopardy is on.
    Unlike the single-target actions this ignores the rule's `target`
    and uses each item's own `device_id`."""
    from app.services.commands import enqueue_for_device

    items = (action or {}).get("items") or []
    applied: list[dict] = []
    skipped: list[dict] = []
    for item in items:
        did = str((item or {}).get("device_id") or "").strip()
        if not did:
            skipped.append({"item": item, "error": "missing device_id"})
            continue
        relay = (item or {}).get("relay")
        config = (item or {}).get("config")
        commands: list[str] = []
        try:
            if relay == "on":
                enqueue_for_device(device_id=did, cmd_type="relay_on",
                                   payload=None, issued_by_user_id=None,
                                   override_lockout=False)
                commands.append("relay_on")
            elif relay == "off":
                enqueue_for_device(device_id=did, cmd_type="relay_off",
                                   payload=None, issued_by_user_id=None,
                                   override_lockout=False)
                commands.append("relay_off")
            elif relay == "cycle":
                enqueue_for_device(
                    device_id=did, cmd_type="relay_cycle",
                    payload={"power_off_seconds": 5,
                             "post_reboot_holdoff_seconds": 180},
                    issued_by_user_id=None, override_lockout=False,
                )
                commands.append("relay_cycle")
            if isinstance(config, dict) and config:
                enqueue_for_device(device_id=did, cmd_type="apply_config",
                                   payload=config, issued_by_user_id=None,
                                   override_lockout=False)
                commands.append("apply_config")
            applied.append({"device_id": did, "commands": commands})
        except Exception as e:
            skipped.append({"device_id": did, "error": str(e)})
    return {
        "action": "apply_scene",
        "rule_id": rule.id,
        "applied": applied,
        "skipped": skipped,
    }


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
