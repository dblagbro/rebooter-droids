"""Aggregate read-only stats for the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db import session_scope
from app.models import (
    AuditEvent,
    Command,
    Device,
    DeviceEvent,
    FirmwareRelease,
    Group,
    Site,
    WatchdogRule,
)


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def stats(online_threshold_seconds: int = 180) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=online_threshold_seconds)
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(Device)) or 0
        online = (
            session.scalar(
                select(func.count()).select_from(Device).where(
                    Device.last_heartbeat_at >= cutoff
                )
            )
            or 0
        )
        active = (
            session.scalar(
                select(func.count()).select_from(Device).where(
                    Device.registration_state == "active"
                )
            )
            or 0
        )
        # v0.2.7: count devices that have NEVER heartbeated separately from
        # those that have-but-are-stale. Lets the UI distinguish "newly
        # enrolled, still waiting for first heartbeat" from "was online,
        # went silent".
        never_heartbeated = (
            session.scalar(
                select(func.count())
                .select_from(Device)
                .where(Device.last_heartbeat_at.is_(None))
            )
            or 0
        )
        with_pending = (
            session.scalar(
                select(func.count(func.distinct(Command.device_id))).where(
                    Command.status.in_(("pending", "accepted", "running"))
                )
            )
            or 0
        )
        groups_count = session.scalar(select(func.count()).select_from(Group)) or 0
        sites_count = session.scalar(select(func.count()).select_from(Site)) or 0
        firmware_count = session.scalar(select(func.count()).select_from(FirmwareRelease)) or 0
        events_24h = (
            session.scalar(
                select(func.count()).select_from(DeviceEvent).where(
                    DeviceEvent.received_at >= now - timedelta(hours=24)
                )
            )
            or 0
        )
        cmds_24h = (
            session.scalar(
                select(func.count()).select_from(Command).where(
                    Command.created_at >= now - timedelta(hours=24)
                )
            )
            or 0
        )
        # v0.5.96: live watchdog-rule counts — the status page's "active
        # rules" tile hardcoded 0 with a stale "watchdogs ship in P4"
        # sub-label since v0.3.1. Watchdogs shipped long ago.
        rules_total = (
            session.scalar(select(func.count()).select_from(WatchdogRule)) or 0
        )
        rules_active = (
            session.scalar(
                select(func.count()).select_from(WatchdogRule).where(
                    WatchdogRule.enabled.is_(True)
                )
            )
            or 0
        )

        return {
            "devices_total": total,
            "devices_online": online,
            # `devices_offline` keeps its v0.2.6 meaning "everything not
            # currently online" — preserves dashboard backwards compat.
            # `devices_offline_with_history` is the strict "was-online-now-
            # silent" subset for any consumer that wants to distinguish.
            "devices_offline": total - online,
            "devices_offline_with_history": max(total - online - never_heartbeated, 0),
            "devices_never_heartbeated": never_heartbeated,
            "devices_active": active,
            "devices_with_pending_commands": with_pending,
            "groups_total": groups_count,
            "sites_total": sites_count,
            "firmware_releases_total": firmware_count,
            "events_24h": events_24h,
            "commands_24h": cmds_24h,
            "rules_total": rules_total,
            "rules_active": rules_active,
            "online_threshold_seconds": online_threshold_seconds,
            "computed_at": _iso(now),
        }


def recent_activity(limit: int = 25) -> list[dict]:
    """Merge audit_events + device_events + commands into one chronological feed."""
    limit = max(1, min(limit, 200))
    items: list[dict] = []

    with session_scope() as session:
        # Audit events (admin actions)
        for e in session.scalars(
            select(AuditEvent).order_by(AuditEvent.at.desc()).limit(limit)
        ):
            items.append(
                {
                    "kind": "admin",
                    "at": _iso(e.at),
                    "actor": e.actor_email_snapshot or "system",
                    "summary": _audit_summary(e),
                    "target_type": e.target_type,
                    "target_id": e.target_id,
                }
            )

        # Recent device events
        for e in session.scalars(
            select(DeviceEvent).order_by(DeviceEvent.received_at.desc()).limit(limit)
        ):
            items.append(
                {
                    "kind": "device",
                    "at": _iso(e.received_at),
                    "actor": e.device_id,
                    "summary": f"{e.type}: {e.message or ''}".rstrip(": "),
                    "target_type": "device",
                    "target_id": e.device_id,
                }
            )

        # Recent commands issued
        for c in session.scalars(
            select(Command).order_by(Command.created_at.desc()).limit(limit)
        ):
            items.append(
                {
                    "kind": "command",
                    "at": _iso(c.created_at),
                    "actor": (c.issued_by_user_id or "system"),
                    "summary": f"{c.type} → {c.device_id} ({c.status})",
                    "target_type": "command",
                    "target_id": c.id,
                }
            )

    items.sort(key=lambda x: x["at"] or "", reverse=True)
    return items[:limit]


def _audit_summary(e: AuditEvent) -> str:
    a = e.action
    d = e.details or {}
    target = e.target_id or ""
    short_t = target[:20] + "…" if len(target) > 22 else target
    if a == "user.invited":
        return f"invited {d.get('email','')} as {d.get('role','')}"
    if a == "user.created_via_invite":
        return f"redeemed invitation → {d.get('email', short_t)}"
    if a == "user.role_changed":
        return f"changed user role → {d.get('new_role','')}"
    if a == "user.deactivated":
        return f"deactivated user {short_t}"
    if a == "user.tokens_revoked":
        return f"revoked tokens for {short_t}"
    if a == "user.display_name_changed":
        return f"renamed user {short_t} → {d.get('new_display_name','')}"
    if a == "device.command_issued":
        return f"issued {d.get('type','')} → {short_t}"
    if a == "device.updated":
        fields = ",".join(d.get("fields") or [])
        return f"updated device {short_t} ({fields})"
    if a == "device.deleted":
        return f"deleted device {short_t}"
    if a == "group.deleted":
        return f"deleted group {short_t}"
    if a == "group.member_removed":
        return f"removed device {d.get('device_id','')[:14]} from group {short_t}"
    if a == "invitation.cancelled":
        return f"cancelled invitation {short_t}"
    return a
