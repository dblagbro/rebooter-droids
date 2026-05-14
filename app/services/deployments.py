from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db import session_scope
from app.models import (
    DeploymentAssignment,
    Device,
    FirmwareDeployment,
    FirmwareRelease,
    GroupMembership,
)

ALLOWED_TARGET_TYPES = ("device", "group", "site", "all_devices")


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_deployment(d: FirmwareDeployment, release: FirmwareRelease | None = None, counts: dict | None = None) -> dict:
    return {
        "id": d.id,
        "release_id": d.release_id,
        "target_type": d.target_type,
        "target_id": d.target_id,
        "channel": d.channel,
        "force": d.force,
        "release": (
            {
                "version": release.version,
                "filename": release.filename,
                "download_url": release.download_url,
                "sha256": release.sha256,
            }
            if release
            else None
        ),
        "counts": counts or {},
        "created_at": _iso(d.created_at),
    }


def create_deployment(
    release_id: str,
    target_type: str,
    target_id: str | None,
    channel: str | None,
    force: bool,
    issued_by_user_id: str | None,
) -> dict:
    if target_type not in ALLOWED_TARGET_TYPES:
        raise ValueError(f"target_type must be one of {ALLOWED_TARGET_TYPES}")
    if target_type != "all_devices" and not target_id:
        raise ValueError("target_id is required for target_type other than all_devices")

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        release = session.get(FirmwareRelease, release_id)
        if release is None:
            raise LookupError(release_id)

        deployment = FirmwareDeployment(
            release_id=release.id,
            target_type=target_type,
            target_id=target_id,
            channel=channel or release.channel,
            force=force,
            issued_by_user_id=issued_by_user_id,
        )
        session.add(deployment)
        session.flush()

        device_ids: list[str] = []
        if target_type == "device":
            d = session.get(Device, target_id)
            if d is None:
                raise LookupError(target_id)
            device_ids = [d.id]
        elif target_type == "group":
            device_ids = list(
                session.scalars(
                    select(GroupMembership.device_id).where(
                        GroupMembership.group_id == target_id
                    )
                )
            )
        elif target_type == "site":
            device_ids = list(
                session.scalars(select(Device.id).where(Device.site_id == target_id))
            )
        elif target_type == "all_devices":
            device_ids = list(session.scalars(select(Device.id)))

        # Supersede any pending assignments for these devices
        if device_ids:
            session.execute(
                update(DeploymentAssignment)
                .where(
                    DeploymentAssignment.device_id.in_(device_ids),
                    DeploymentAssignment.state.in_(("pending", "delivered")),
                )
                .values(state="superseded", updated_at=now)
            )

        for did in device_ids:
            session.add(
                DeploymentAssignment(
                    deployment_id=deployment.id,
                    release_id=release.id,
                    device_id=did,
                    state="pending",
                )
            )
        session.flush()
        return serialize_deployment(
            deployment,
            release=release,
            counts={"target_devices": len(device_ids)},
        )


def list_deployments() -> list[dict]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(FirmwareDeployment).order_by(FirmwareDeployment.created_at.desc())
            )
        )
        out = []
        for d in rows:
            release = session.get(FirmwareRelease, d.release_id)
            assignments = list(
                session.scalars(
                    select(DeploymentAssignment).where(
                        DeploymentAssignment.deployment_id == d.id
                    )
                )
            )
            counts = {
                "total": len(assignments),
                "pending": sum(1 for a in assignments if a.state == "pending"),
                "delivered": sum(1 for a in assignments if a.state == "delivered"),
                "completed": sum(1 for a in assignments if a.state == "completed"),
                "failed": sum(1 for a in assignments if a.state == "failed"),
                "superseded": sum(1 for a in assignments if a.state == "superseded"),
            }
            out.append(serialize_deployment(d, release=release, counts=counts))
        return out


def assignment_for_device(device_id: str) -> dict | None:
    """Return the active firmware assignment for the device, or None."""
    with session_scope() as session:
        a = session.scalar(
            select(DeploymentAssignment)
            .where(
                DeploymentAssignment.device_id == device_id,
                DeploymentAssignment.state.in_(("pending", "delivered")),
            )
            .order_by(DeploymentAssignment.created_at.desc())
            .limit(1)
        )
        if a is None:
            return None
        release = session.get(FirmwareRelease, a.release_id)
        deployment = session.get(FirmwareDeployment, a.deployment_id)
        if not release or not deployment:
            return None
        return {
            "assignment_id": a.id,
            "deployment_id": deployment.id,
            "channel": deployment.channel,
            "target_version": release.version,
            "download_url": release.download_url,
            "sha256": release.sha256,
            "force": deployment.force,
        }


def mark_assignment_delivered(device_id: str) -> None:
    with session_scope() as session:
        a = session.scalar(
            select(DeploymentAssignment)
            .where(
                DeploymentAssignment.device_id == device_id,
                DeploymentAssignment.state == "pending",
            )
            .order_by(DeploymentAssignment.created_at.desc())
            .limit(1)
        )
        if a is not None:
            a.state = "delivered"
            a.updated_at = datetime.now(timezone.utc)
            session.add(a)


def reconcile_assignment_reported_version(
    session,
    device_id: str,
    reported_version: str | None,
    *,
    error_message: str | None = None,
    reported_at: datetime | None = None,
) -> None:
    """Reconcile the active firmware assignment against the device's
    latest reported firmware version.

    The live soak surfaced a gap where deployments moved from
    `pending` -> `delivered` when the device fetched `/device/firmware`,
    but never advanced to `completed` after the device came back on the
    target version. Heartbeat is the most trustworthy completion signal
    we already have on the hub side, so we close the loop here.
    """
    normalized_version = (reported_version or "").strip() or None
    if not device_id:
        return

    a = session.scalar(
        select(DeploymentAssignment)
        .where(
            DeploymentAssignment.device_id == device_id,
            DeploymentAssignment.state.in_(("pending", "delivered")),
        )
        .order_by(DeploymentAssignment.created_at.desc())
        .limit(1)
    )
    if a is None:
        return

    now = reported_at or datetime.now(timezone.utc)
    changed = False
    if a.last_reported_version != normalized_version:
        a.last_reported_version = normalized_version
        changed = True

    if error_message and a.error_message != error_message:
        a.error_message = error_message
        changed = True

    release = session.get(FirmwareRelease, a.release_id)
    target_version = (release.version or "").strip() if release else ""
    if normalized_version and target_version and normalized_version == target_version:
        if a.state != "completed":
            a.state = "completed"
            changed = True
        if a.error_message is not None:
            a.error_message = None
            changed = True

    if changed:
        a.updated_at = now
        session.add(a)
