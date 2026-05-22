"""Read-only queries over the device aggregate.

Everything in this module opens its own `session_scope()` and returns
dicts (not ORM rows). The two `_*_by_device` helpers take an already-
open session because they are reused inside `list_devices` /
`get_device_detail`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from app.db import session_scope
from app.models import (
    Command,
    Device,
    DeviceEvent,
    DeviceHeartbeat,
    DeploymentAssignment,
    FirmwareRelease,
    Group,
    GroupMembership,
)
from app.services._versions import _version_sort_key
from app.services.devices._serialize import (
    _derive_central_status,
    _heartbeat_state_for,
    _iso,
    _serialize_assignment,
    serialize_device,
)


def _latest_heartbeat_by_device(
    session, device_ids: Iterable[str]
) -> dict[str, DeviceHeartbeat]:
    """v0.5.14 (B18): fetch the most-recent DeviceHeartbeat row for each
    device. Returned dict is keyed by device_id; devices with no
    heartbeats are absent. One query per call (not per device)."""
    ids = [d for d in device_ids if d]
    if not ids:
        return {}
    rows = list(
        session.scalars(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id.in_(ids))
            .order_by(
                DeviceHeartbeat.device_id.asc(),
                DeviceHeartbeat.received_at.desc(),
            )
        )
    )
    latest: dict[str, DeviceHeartbeat] = {}
    for row in rows:
        # Order-by puts newest first per device_id; setdefault wins.
        latest.setdefault(row.device_id, row)
    return latest


def _active_assignments_by_device(session, device_ids: Iterable[str]) -> dict[str, dict]:
    """Latest pending|delivered firmware assignment per device, serialized
    to the dict shape `_derive_central_status` consumes."""
    ids = [d for d in device_ids if d]
    if not ids:
        return {}
    rows = list(
        session.scalars(
            select(DeploymentAssignment)
            .where(
                DeploymentAssignment.device_id.in_(ids),
                DeploymentAssignment.state.in_(("pending", "delivered")),
            )
            .order_by(
                DeploymentAssignment.device_id.asc(),
                DeploymentAssignment.created_at.desc(),
            )
        )
    )
    latest_by_device: dict[str, DeploymentAssignment] = {}
    for row in rows:
        latest_by_device.setdefault(row.device_id, row)

    release_ids = {row.release_id for row in latest_by_device.values()}
    releases = {
        r.id: r
        for r in session.scalars(
            select(FirmwareRelease).where(FirmwareRelease.id.in_(release_ids))
        )
    } if release_ids else {}
    return {
        device_id: _serialize_assignment(row, releases.get(row.release_id))
        for device_id, row in latest_by_device.items()
    }


def find_by_mac(mac_address: str | None) -> list[dict]:
    """v0.5.7 (B20): lookup existing devices by MAC. Used by
    pending-adoption to surface "this hardware is already in the fleet"
    matches so the operator can pick rebind vs fresh-adopt explicitly.
    Excludes rows in registration_state='decommissioned' (post-replacement
    marker).

    v0.6.3 (devices-page perf): pre-0.6.3 this loaded EVERY device with a
    non-NULL MAC and filtered (MAC equality + decommissioned exclusion)
    in Python — on a large fleet, the whole devices table streamed into
    the app per call, and the pending-adoption page calls this once per
    announcement row. Both predicates now run in SQL:

      * MAC equality is case- and surrounding-whitespace-insensitive,
        matching the old `mac_address.strip().upper()` comparison via
        `upper(trim(mac_address)) = :mac`.
      * the `decommissioned` exclusion is a plain `WHERE` clause.

    The result set is identical — same rows, same serialization."""
    if not mac_address:
        return []
    mac = mac_address.strip().upper()
    if not mac:
        return []
    from sqlalchemy import func

    with session_scope() as session:
        rows = list(session.scalars(
            select(Device).where(
                Device.mac_address.is_not(None),
                func.upper(func.trim(Device.mac_address)) == mac,
                Device.registration_state != "decommissioned",
            )
        ))
        return [
            serialize_device(d, include_secret_status=False)
            for d in rows
        ]


def latest_stable_release_dict() -> dict | None:
    """v0.4.29: highest-version release in the `stable` channel (or None).

    Pre-v0.4.29 this returned the most-recently-*uploaded* release, which
    surfaced a "Upgrade" button that was sometimes a downgrade when an
    older release got re-uploaded after a newer one. The selection is
    therefore by VERSION ORDER, not upload time.

    v0.6.3 (devices-page perf): pre-0.6.3 this loaded every stable
    `FirmwareRelease` as a full ORM object (every column, incl. the Text
    release_notes) just to pick the highest version. It now:

      * selects ONLY the six columns the result dict needs, and
      * narrows the candidate set in SQL with `ORDER BY created_at DESC
        LIMIT 200` so a fleet that has accumulated thousands of stable
        re-uploads no longer streams the whole table into Python.

    The final highest-version pick stays in Python via `_version_sort_key`
    — the version string is `N.N.N[-suffix]` and its correct ordering is
    NUMERIC by dotted-int prefix (so `0.1.10` > `0.1.9`). That ordering
    cannot be expressed in a single portable SQL `ORDER BY` (Postgres and
    SQLite have no shared numeric-version collation, and there is no
    stored numeric key column), and correctness of the version ordering
    is non-negotiable — a wrong pick re-introduces the v0.4.29
    downgrade-button bug. So the `LIMIT` bounds the work and the
    `_version_sort_key` pick keeps it correct. 200 newest uploads is far
    more than any real release history and guarantees the true latest is
    in the window."""
    with session_scope() as session:
        rows = list(
            session.execute(
                select(
                    FirmwareRelease.id,
                    FirmwareRelease.version,
                    FirmwareRelease.channel,
                    FirmwareRelease.sha256,
                    FirmwareRelease.size_bytes,
                    FirmwareRelease.filename,
                )
                .where(FirmwareRelease.channel == "stable")
                .order_by(FirmwareRelease.created_at.desc())
                .limit(200)
            )
        )
        if not rows:
            return None
        rel = max(rows, key=lambda r: _version_sort_key(r.version))
        return {
            "id": rel.id,
            "version": rel.version,
            "channel": rel.channel,
            "sha256": rel.sha256,
            "size_bytes": rel.size_bytes,
            "filename": rel.filename,
        }


def firmware_version_breakdown(*, include_qa_fixtures: bool = False) -> list[dict]:
    """v0.4.19 (Tier-1 A): group the fleet by `firmware_version`. Surfaces
    "which devices on which version" so the operator can spot upgrade
    outliers at a glance. Largest cohort gets `is_majority=true` so the
    UI can mark outliers.

    v0.6.3 (devices-page perf): pre-0.6.3 this loaded every Device row as
    a full ORM object (every column) just to read three fields and group
    in Python. It now selects ONLY `(id, display_name, firmware_version)`
    — the columns it actually uses — and the grouping/count is still
    done in Python over that minimal projection. The bucket order, the
    per-version `devices` lists, the `(unknown)` collapse and the
    `is_majority` flag are byte-for-byte identical to before; only the
    column footprint of the query shrank."""
    with session_scope() as session:
        stmt = select(
            Device.id, Device.display_name, Device.firmware_version
        )
        if not include_qa_fixtures:
            stmt = stmt.where(Device.is_qa_fixture.is_(False))
        rows = list(session.execute(stmt))

    buckets: dict[str, list[dict]] = {}
    for device_id, display_name, firmware_version in rows:
        ver = (firmware_version or "(unknown)").strip() or "(unknown)"
        buckets.setdefault(ver, []).append({
            "id": device_id,
            "display_name": display_name or device_id,
        })

    if not buckets:
        return []

    breakdown = [
        {
            "version": ver,
            "count": len(devs),
            "devices": sorted(devs, key=lambda x: x["display_name"]),
        }
        for ver, devs in buckets.items()
    ]
    breakdown.sort(key=lambda b: (-b["count"], b["version"]))
    majority_count = breakdown[0]["count"]
    for b in breakdown:
        b["is_majority"] = (b["count"] == majority_count and len(breakdown) > 1)
    return breakdown


def list_devices(
    site_id: str | None = None,
    group_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    offline_threshold_seconds: int = 180,
    include_qa_fixtures: bool = True,
    chips: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    """v0.3.1 (P2) saved-filter `chips` AND-compose with the other args.

    Recognised chips:
      - "offline_24h"   — last_heartbeat_at older than 24 h (had history)
      - "never"         — last_heartbeat_at IS NULL
      - "pending_cmds"  — has a command in pending/accepted/running state
      - "qa_fixtures"   — only QA-fixture-tagged rows (overrides
                          include_qa_fixtures=False to *show* them)

    Unrecognised chip names are silently ignored so a stale URL doesn't 500.
    """
    now = datetime.now(timezone.utc)
    chips = tuple(c for c in (chips or ()) if c)
    with session_scope() as session:
        stmt = select(Device)
        if site_id:
            stmt = stmt.where(Device.site_id == site_id)
        if status == "active":
            stmt = stmt.where(Device.registration_state == "active")
        if status == "disabled":
            stmt = stmt.where(Device.registration_state == "disabled")

        from datetime import timedelta as _td

        if "qa_fixtures" in chips:
            stmt = stmt.where(Device.is_qa_fixture.is_(True))
        elif not include_qa_fixtures:
            stmt = stmt.where(Device.is_qa_fixture.is_(False))

        if "offline_24h" in chips:
            cutoff_24h = now - _td(hours=24)
            stmt = stmt.where(
                Device.last_heartbeat_at.is_not(None),
                Device.last_heartbeat_at < cutoff_24h,
            )

        if "never" in chips:
            stmt = stmt.where(Device.last_heartbeat_at.is_(None))

        if "pending_cmds" in chips:
            from sqlalchemy import exists

            stmt = stmt.where(
                exists().where(
                    (Command.device_id == Device.id)
                    & (Command.status.in_(("pending", "accepted", "running")))
                )
            )

        if search:
            like = f"%{search.lower()}%"
            from sqlalchemy import or_, func

            stmt = stmt.where(
                or_(
                    func.lower(Device.display_name).like(like),
                    func.lower(Device.mac_address).like(like),
                    func.lower(Device.id).like(like),
                )
            )

        if group_id:
            stmt = stmt.join(
                GroupMembership, GroupMembership.device_id == Device.id
            ).where(GroupMembership.group_id == group_id)

        stmt = stmt.order_by(Device.created_at.desc())

        # v0.5.37 (B1 RBAC Phase 3): Apply scope-based filtering.
        # In shadow mode, logs what WOULD be hidden; in enforce mode, actually filters.
        from app.services.rbac_filter import filter_devices_with_shadow_logging
        rows = filter_devices_with_shadow_logging(stmt, session)

        device_ids = [d.id for d in rows]
        assignments_by_device = _active_assignments_by_device(session, device_ids)
        heartbeats_by_device = _latest_heartbeat_by_device(session, device_ids)
        # v0.5.26 (B16 Phase 1A): latest power sample per device, batched.
        # Imported lazily — keeps the import graph small for callers that
        # don't render the devices list (e.g. internal jobs).
        from app.services import device_power

        power_samples_by_device = device_power.latest_samples_by_device(device_ids)
        out = []
        for d in rows:
            obj = serialize_device(d)
            hb_state = _heartbeat_state_for(
                d.last_heartbeat_at,
                now=now,
                offline_threshold_seconds=offline_threshold_seconds,
                last_seen_at=d.last_seen_at,
            )
            obj["heartbeat_state"] = hb_state
            obj["online"] = hb_state == "online"
            # v0.5.14 (B18): surface relay_on + mode for the inline toggle.
            latest_hb = heartbeats_by_device.get(d.id)
            obj["latest_relay_on"] = bool(latest_hb.relay_on) if latest_hb else None
            obj["latest_mode"] = latest_hb.mode if latest_hb else None
            assignment = assignments_by_device.get(d.id)
            if assignment:
                obj["active_firmware_assignment"] = assignment
            central_status = _derive_central_status(
                d,
                heartbeat_state=hb_state,
                latest_health_state=(latest_hb.health_state if latest_hb else None),
                active_assignment=assignment,
            )
            obj["central_status"] = central_status["code"]
            obj["central_status_label"] = central_status["label"]
            obj["central_status_reason"] = central_status["reason"]
            obj["central_status_class"] = central_status["badge_class"]
            # v0.5.26: latest_power_sample is None when the device has
            # never reported, present (with is_stale flag) otherwise.
            obj["latest_power_sample"] = power_samples_by_device.get(d.id)
            # v0.5.31 (Phase 4A): desired-config drift summary for the
            # devices-list chip. Cheap to compute inline since the JSON
            # blobs are already loaded on the Device row.
            obj["desired_config_set"] = bool(d.desired_config)
            obj["desired_config_drift_summary"] = None
            if d.central_management_enabled and d.desired_config:
                reported = d.last_reported_config or {}
                if not isinstance(reported, dict) or not reported:
                    obj["desired_config_drift_summary"] = {
                        "state": "unconfirmed",
                        "missing": list(d.desired_config.keys()),
                        "mismatched": [],
                    }
                else:
                    missing: list[str] = []
                    mismatched: list[str] = []
                    for field, want in d.desired_config.items():
                        if field not in reported:
                            missing.append(field)
                        elif reported.get(field) != want:
                            mismatched.append(field)
                    if missing or mismatched:
                        obj["desired_config_drift_summary"] = {
                            "state": "drifted",
                            "missing": missing,
                            "mismatched": mismatched,
                        }
                    else:
                        obj["desired_config_drift_summary"] = {
                            "state": "in_sync",
                            "missing": [],
                            "mismatched": [],
                        }
            out.append(obj)
        return out


def get_device_detail(device_id: str) -> dict | None:
    """Single-device detail with latest heartbeat, group memberships,
    recent events, pending commands, audit slice, and failsafe history.

    Audit + failsafe services are imported lazily to keep this module's
    import graph shallow (matches the original devices.py behavior)."""
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        out = serialize_device(d)
        now = datetime.now(timezone.utc)
        hb_state = _heartbeat_state_for(
            d.last_heartbeat_at,
            now=now,
            offline_threshold_seconds=180,
            last_seen_at=d.last_seen_at,
        )
        out["heartbeat_state"] = hb_state
        out["online"] = hb_state == "online"

        latest_hb = session.scalar(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id == device_id)
            .order_by(DeviceHeartbeat.received_at.desc())
            .limit(1)
        )
        out["latest_heartbeat"] = (
            {
                "received_at": _iso(latest_hb.received_at),
                "mode": latest_hb.mode,
                "relay_on": latest_hb.relay_on,
                "wifi_connected": latest_hb.wifi_connected,
                "health_state": latest_hb.health_state,
                "uptime_seconds": latest_hb.uptime_seconds,
                "incident_cycles": latest_hb.incident_cycles,
                "hour_cycles": latest_hb.hour_cycles,
                "last_event_type": latest_hb.last_event_type,
                "last_event_at": _iso(latest_hb.last_event_at),
            }
            if latest_hb
            else None
        )
        assignment = _active_assignments_by_device(session, [device_id]).get(device_id)
        if assignment:
            out["active_firmware_assignment"] = assignment
        central_status = _derive_central_status(
            d,
            heartbeat_state=hb_state,
            latest_health_state=(latest_hb.health_state if latest_hb else None),
            active_assignment=assignment,
        )
        out["central_status"] = central_status["code"]
        out["central_status_label"] = central_status["label"]
        out["central_status_reason"] = central_status["reason"]
        out["central_status_class"] = central_status["badge_class"]

        group_rows = list(
            session.execute(
                select(Group)
                .join(GroupMembership, GroupMembership.group_id == Group.id)
                .where(GroupMembership.device_id == device_id)
            )
        )
        out["groups"] = [{"id": g[0].id, "name": g[0].name} for g in group_rows]

        recent_events = list(
            session.scalars(
                select(DeviceEvent)
                .where(DeviceEvent.device_id == device_id)
                .order_by(DeviceEvent.timestamp.desc())
                .limit(20)
            )
        )
        out["recent_events"] = [
            {
                "type": e.type,
                "timestamp": _iso(e.timestamp),
                "message": e.message,
                "mode": e.mode,
                "details": e.details,
            }
            for e in recent_events
        ]

        pending_cmds = list(
            session.scalars(
                select(Command)
                .where(
                    Command.device_id == device_id,
                    Command.status.in_(("pending", "accepted", "running")),
                )
                .order_by(Command.created_at.desc())
            )
        )
        out["pending_commands"] = [
            {
                "id": c.id,
                "type": c.type,
                "status": c.status,
                "created_at": _iso(c.created_at),
                "expires_at": _iso(c.expires_at),
                "payload": c.payload,
            }
            for c in pending_cmds
        ]

        # v0.2.9: per-record audit slice (composite index ix_audit_target).
        from app.services import audit as audit_service

        out["audit_history"] = audit_service.query(
            target_type="device", target_id=device_id, limit=25
        )

        # v0.3.8 (RFC-005 P1): per-device failsafe history.
        from app.services import failsafe as failsafe_service

        out["failsafe_events"] = failsafe_service.list_for_device(
            device_id, limit=25
        )

        # v0.5.22 (B21): desired-config blob + drift snapshot. Imported
        # lazily so the package's load-time graph isn't enlarged for
        # callers that don't render a device-detail view.
        from app.services import device_config

        out["desired_config"] = dict(d.desired_config) if d.desired_config else {}
        out["desired_mode"] = d.desired_mode
        out["last_reported_config"] = (
            dict(d.last_reported_config) if d.last_reported_config else {}
        )
        out["desired_config_updated_at"] = _iso(d.desired_config_updated_at)
        out["last_config_pushed_at"] = _iso(d.last_config_pushed_at)
        out["desired_config_drift"] = device_config.compute_drift(device_id)
        out["desired_config_feature_enabled"] = device_config.is_feature_enabled()

        # v0.5.26 (B16 Phase 1A): latest power sample for the Power tab
        # live-card. Imported lazily — keeps the package import graph
        # small for callers that don't render device-detail.
        from app.services import device_power

        out["latest_power_sample"] = device_power.latest_sample(device_id)
        # v0.5.29 (B16 Phase 1C): daily rollups for the sparkline.
        # 14 days so the chart can show meaningful trend; rendered as
        # inline SVG in the template.
        out["power_rollups_daily"] = device_power.daily_rollups_for_device(
            device_id, days=14
        )
        # v0.5.55 (P1.2): real-vs-synthetic sample mix over the last 24h,
        # for the Power-section data-quality line.
        out["power_source_breakdown"] = device_power.power_source_breakdown(
            device_id, window_seconds=24 * 60 * 60
        )
        # v0.5.59 (P1.3): time-bucketed 24h watts series for the
        # interactive intraday chart on the Power section.
        out["power_intraday_series"] = device_power.intraday_power_series(
            device_id, window_seconds=24 * 60 * 60
        )
        return out
