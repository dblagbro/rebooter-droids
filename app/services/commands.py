from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db import session_scope
from app.models import Command, CommandResult, Device, GroupMembership

DEFAULT_TTL_SECONDS = 600
ALLOWED_TYPES = {
    "relay_on",
    "relay_off",
    "relay_toggle",
    "relay_cycle",
    "device_restart",
    "factory_reset",
    "set_mode",
    "apply_config",
    "check_firmware",
    "start_firmware_update",
}
ALLOWED_RESULT_STATUSES = {"accepted", "running", "completed", "failed", "expired"}

# Locked v0.1 schemas — agreed with firmware/design team 2026-05-09.
SET_MODE_ALLOWED = {"smart_plug", "internet_watchdog", "device_watchdog"}
APPLY_CONFIG_ALLOWED_TOP_LEVEL = {
    "device_name",
    "relay_restore_behavior",
    "monitor_interval_seconds",
    "boot_warmup_seconds",
    "manual_button_enabled",
    "internet",
    "device",
    "notifications",
}


def _validate_payload(cmd_type: str, payload: dict | None) -> dict:
    payload = payload or {}
    if cmd_type == "set_mode":
        mode = payload.get("mode")
        if mode not in SET_MODE_ALLOWED:
            raise ValueError(
                f"set_mode.mode must be one of {sorted(SET_MODE_ALLOWED)}"
            )
        return {"mode": mode}
    if cmd_type == "apply_config":
        if not isinstance(payload, dict) or not payload:
            raise ValueError("apply_config payload must be a non-empty object")
        unknown = set(payload.keys()) - APPLY_CONFIG_ALLOWED_TOP_LEVEL
        if unknown:
            raise ValueError(
                "apply_config contains unsupported top-level keys: "
                + ", ".join(sorted(unknown))
                + f". Allowed: {sorted(APPLY_CONFIG_ALLOWED_TOP_LEVEL)}"
            )
        return payload
    if cmd_type == "relay_cycle":
        # Light schema check — the firmware team's defaults.
        for k in ("power_off_seconds", "post_reboot_holdoff_seconds"):
            if k in payload and not isinstance(payload[k], int):
                raise ValueError(f"relay_cycle.{k} must be an integer")
        return payload
    return payload


def enqueue_for_device(
    device_id: str,
    cmd_type: str,
    payload: dict | None,
    issued_by_user_id: str | None,
    ttl_seconds: int | None = None,
    group_id: str | None = None,
) -> Command:
    if cmd_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported command type: {cmd_type}")
    payload = _validate_payload(cmd_type, payload)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds or DEFAULT_TTL_SECONDS)
    cmd = Command(
        device_id=device_id,
        group_id=group_id,
        issued_by_user_id=issued_by_user_id,
        type=cmd_type,
        payload=payload,
        status="pending",
        expires_at=expires,
    )
    with session_scope() as session:
        target = session.get(Device, device_id)
        if target is None:
            raise LookupError(device_id)
        session.add(cmd)
        session.flush()
        session.expunge(cmd)
    return cmd


def enqueue_for_group(
    group_id: str,
    cmd_type: str,
    payload: dict | None,
    issued_by_user_id: str | None,
    ttl_seconds: int | None = None,
) -> list[Command]:
    if cmd_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported command type: {cmd_type}")
    payload = _validate_payload(cmd_type, payload)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds or DEFAULT_TTL_SECONDS)
    created: list[Command] = []
    with session_scope() as session:
        device_ids = list(
            session.scalars(
                select(GroupMembership.device_id).where(GroupMembership.group_id == group_id)
            )
        )
        for did in device_ids:
            cmd = Command(
                device_id=did,
                group_id=group_id,
                issued_by_user_id=issued_by_user_id,
                type=cmd_type,
                payload=payload,
                status="pending",
                expires_at=expires,
            )
            session.add(cmd)
            created.append(cmd)
        session.flush()
        for c in created:
            session.expunge(c)
    return created


def list_pending_for_device(device_id: str, mark_delivered: bool = True) -> list[Command]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(Command)
                .where(
                    Command.device_id == device_id,
                    Command.status.in_(("pending", "accepted", "running")),
                    Command.expires_at > now,
                )
                .order_by(Command.created_at.asc())
            )
        )
        if mark_delivered:
            for r in rows:
                if r.status == "pending":
                    r.status = "accepted"
                    r.delivered_at = now
                    session.add(r)
            session.flush()
        for r in rows:
            session.expunge(r)
    return rows


def record_result(
    device_id: str,
    command_id: str,
    status: str,
    message: str | None,
    result: dict | None,
    completed_at: datetime | None,
) -> CommandResult:
    if status not in ALLOWED_RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    completed_dt = completed_at or datetime.now(timezone.utc)
    with session_scope() as session:
        cmd = session.get(Command, command_id)
        if cmd is None or cmd.device_id != device_id:
            raise LookupError(command_id)
        cr = CommandResult(
            command_id=command_id,
            device_id=device_id,
            status=status,
            message=message,
            result=result or {},
            completed_at=completed_dt,
        )
        cmd.status = status
        cmd.completed_at = completed_dt
        session.add(cmd)
        session.add(cr)
        session.flush()
        session.expunge(cr)
    return cr


def expire_overdue_commands() -> int:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        result = session.execute(
            update(Command)
            .where(
                Command.status.in_(("pending", "accepted", "running")),
                Command.expires_at <= now,
            )
            .values(status="expired")
        )
        return result.rowcount or 0
