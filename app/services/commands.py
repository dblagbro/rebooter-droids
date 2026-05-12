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
    # v0.5.6: LAN-bridge commands — firmware ≥ 0.1.11 handles these.
    # A "bridge" device (one that's heartbeating) executes them
    # against other devices on its own LAN, returning the result via
    # /device/command-result. Unlocks remote fleet recovery without
    # the operator needing LAN access. See firmware-team note
    # 2026-05-12 for the device-side endpoints these wrap.
    "lan_scan",       # scan LAN subnet for live rebooter devices
    "lan_proxy",      # proxy an HTTP request to a LAN peer
    "lan_ota_push",   # tell a LAN peer to OTA-pull a firmware URL
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
    # v0.5.6: LAN-bridge commands. Light schema validation — these
    # are operator-triggered recovery commands, not customer-facing,
    # so we trust the caller more than we trust device-facing
    # endpoints.
    if cmd_type == "lan_scan":
        start = payload.get("start")
        end = payload.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("lan_scan requires integer 'start' and 'end'")
        if not (1 <= start <= 254 and 1 <= end <= 254 and start <= end):
            raise ValueError("lan_scan start/end must be in 1..254 with start <= end")
        if end - start > 254:
            raise ValueError("lan_scan range too wide (max 254 IPs)")
        return {"start": start, "end": end}
    if cmd_type == "lan_proxy":
        ip = payload.get("ip")
        path = payload.get("path")
        if not isinstance(ip, str) or not ip:
            raise ValueError("lan_proxy requires non-empty 'ip'")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("lan_proxy 'path' must be a string starting with '/'")
        method = (payload.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            raise ValueError("lan_proxy 'method' must be GET or POST")
        out = {"ip": ip, "path": path, "method": method}
        if "body" in payload:
            out["body"] = payload["body"]
        if "headers" in payload and isinstance(payload["headers"], dict):
            out["headers"] = payload["headers"]
        return out
    if cmd_type == "lan_ota_push":
        ip = payload.get("ip")
        url = payload.get("url")
        if not isinstance(ip, str) or not ip:
            raise ValueError("lan_ota_push requires non-empty 'ip'")
        if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("lan_ota_push 'url' must be a http(s) URL")
        out = {"ip": ip, "url": url}
        if "sha256" in payload:
            out["sha256"] = payload["sha256"]
        return out
    return payload


_POWER_TYPES: frozenset[str] = frozenset({
    "relay_on", "relay_off", "relay_toggle", "relay_cycle", "device_restart",
})
_POWER_ON_TYPES: frozenset[str] = frozenset({
    "relay_on", "relay_toggle", "relay_cycle",
})


class DeviceLockedError(Exception):
    """v0.3.2 (P3): power command rejected because the device is
    `is_protected`. Caller must opt in via override_lockout=True."""


def enqueue_for_device(
    device_id: str,
    cmd_type: str,
    payload: dict | None,
    issued_by_user_id: str | None,
    ttl_seconds: int | None = None,
    group_id: str | None = None,
    override_lockout: bool = False,
    set_hold_off: bool = False,
) -> Command:
    """v0.3.2 (P3): is_protected gate + is_held_off side effect.

    - If the device's `is_protected` is True and `cmd_type` is in
      `_POWER_TYPES`, the call raises DeviceLockedError unless
      `override_lockout=True`.
    - If `set_hold_off=True`, the device's `is_held_off` flag is
      flipped on as part of the same transaction. Used by the UI's
      "hold off until manually restored" button.
    - Power-on commands (relay_on / relay_toggle / relay_cycle)
      automatically clear `is_held_off` — the operator's act of
      turning it back on IS the clear.
    """
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
        if cmd_type in _POWER_TYPES and target.is_protected and not override_lockout:
            raise DeviceLockedError(
                f"device {device_id} is protected; override_lockout=True required"
            )
        # Hold-off semantics
        if set_hold_off:
            target.is_held_off = True
            session.add(target)
        elif cmd_type in _POWER_ON_TYPES and target.is_held_off:
            target.is_held_off = False
            session.add(target)
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
    override_lockout: bool = False,
) -> tuple[list[Command], list[str]]:
    """v0.3.2 (P3): mass fan-out skips locked devices unless override.
    Returns (created_commands, skipped_device_ids) — UI surfaces the
    skipped count in the mass-action confirmation flow."""
    if cmd_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported command type: {cmd_type}")
    payload = _validate_payload(cmd_type, payload)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds or DEFAULT_TTL_SECONDS)
    created: list[Command] = []
    skipped: list[str] = []
    with session_scope() as session:
        device_ids = list(
            session.scalars(
                select(GroupMembership.device_id).where(GroupMembership.group_id == group_id)
            )
        )
        for did in device_ids:
            target = session.get(Device, did)
            if target is None:
                continue
            if cmd_type in _POWER_TYPES and target.is_protected and not override_lockout:
                skipped.append(did)
                continue
            if cmd_type in _POWER_ON_TYPES and target.is_held_off:
                target.is_held_off = False
                session.add(target)
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
    return created, skipped


def cancel_pending_command(command_id: str, by_user_id: str | None) -> bool:
    """v0.3.2 (P3): operator-cancel a queued command before delivery.

    Only commands in `pending` status can be cancelled — once the
    device has accepted (status='accepted') we can't pull it back.
    Returns True on success, False if the command isn't found or is
    no longer cancellable.
    """
    with session_scope() as session:
        cmd = session.get(Command, command_id)
        if cmd is None or cmd.status != "pending":
            return False
        cmd.status = "cancelled"
        cmd.completed_at = datetime.now(timezone.utc)
        session.add(cmd)
        session.flush()
        return True


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
