"""Admin JSON API handlers for power telemetry — v0.5.54 (P1.1).

Read-only endpoints over the power-telemetry query layer in
``app/services/device_power.py``. Until now those query functions fed
only server-rendered admin pages (device-detail Power tab, fleet
``/app/power``); there was no JSON surface for them.

Endpoints (all under ``/api/v1/admin``):

  GET /devices/<id>/power-samples   — windowed raw samples
  GET /devices/<id>/power-rollups   — recent daily rollups
  GET /power/summary                — fleet aggregate (24h/7d/30d)

Response-envelope note: each payload carries an explicit
``modality: "power"`` tag and a window descriptor. Per the hub-team
plan §6 (P3), this is the seam the future cross-modal query layer
reuses — keeping the envelope modality-tagged now means a second
modality can be added without reshaping these responses.
"""

from __future__ import annotations

from flask import request

from app.blueprints.admin import admin_api_bp
from app.db import session_scope
from app.middleware.admin_auth import admin_required_api, scope_required_api
from app.middleware.response import err, ok
from app.models import Device
from app.models.users import ROLE_VIEWER
from app.services import device_power


def _int_arg(name: str, default: int) -> int:
    """Parse an int query param; fall back to `default` on absence or
    a non-integer value. Range-clamping is left to the service layer."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _device_exists(device_id: str) -> bool:
    with session_scope() as session:
        return session.get(Device, device_id) is not None


@admin_api_bp.get("/devices/<device_id>/power-samples")
@admin_required_api
@scope_required_api(ROLE_VIEWER, scope="device", id_kwarg="device_id")
def get_device_power_samples(device_id: str):
    """Windowed raw power samples for one device, newest-first.

    Query params: `window_seconds` (default 3600, clamped 60..86400 by
    the service), `limit` (default 720), `channel_id` (default 0),
    `source` (optional — restrict to one source, e.g. `steady`).

    v0.5.55 (P1.2): each sample carries `source_kind` (real/synthetic)
    and `source_flags_decoded`; the response includes a
    `source_breakdown` over the full window so a caller never silently
    merges real and synthetic telemetry.
    """
    if not _device_exists(device_id):
        return err("device_unknown", "Device not found.", status=404)
    window_seconds = _int_arg("window_seconds", device_power.RECENT_WINDOW_DEFAULT_SECONDS)
    limit = _int_arg("limit", 720)
    channel_id = _int_arg("channel_id", 0)
    source = request.args.get("source") or None
    samples = device_power.recent_samples(
        device_id,
        channel_id=channel_id,
        window_seconds=window_seconds,
        limit=limit,
        source=source,
    )
    breakdown = device_power.power_source_breakdown(
        device_id, channel_id=channel_id, window_seconds=window_seconds
    )
    return ok({
        "device_id": device_id,
        "modality": "power",
        "channel_id": channel_id,
        "window_seconds": window_seconds,
        "source_filter": source,
        "sample_count": len(samples),
        "source_breakdown": breakdown,
        "samples": samples,
    })


@admin_api_bp.get("/devices/<device_id>/power-rollups")
@admin_required_api
@scope_required_api(ROLE_VIEWER, scope="device", id_kwarg="device_id")
def get_device_power_rollups(device_id: str):
    """Recent daily power rollups for one device, newest-first.

    Query param: `days` (default 7, clamped 1..365 by the service).
    """
    if not _device_exists(device_id):
        return err("device_unknown", "Device not found.", status=404)
    days = _int_arg("days", 7)
    rollups = device_power.daily_rollups_for_device(device_id, days=days)
    return ok({
        "device_id": device_id,
        "modality": "power",
        "days": days,
        "rollup_count": len(rollups),
        "rollups": rollups,
    })


@admin_api_bp.get("/power/summary")
@admin_required_api
def get_power_summary():
    """Fleet-wide power aggregate over a window.

    Query param: `window_seconds` (default 86400 = 24h; clamped
    60..2592000 by the service — supports the 24h/7d/30d UI windows).

    Note: not scope-filtered — matches the fleet `/app/power` page,
    which is also fleet-wide. Per-device scope filtering of the fleet
    summary is a tracked follow-up.
    """
    window_seconds = _int_arg("window_seconds", 24 * 60 * 60)
    summary = device_power.fleet_summary(window_seconds=window_seconds)
    summary["modality"] = "power"
    return ok(summary)
