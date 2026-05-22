"""Pure-function serialization + presentation helpers.

No DB writes; no session_scope() calls. Anything that turns a SQLAlchemy
row into the dict shape the API + templates consume lives here.

Public symbols are re-exported from `app.services.devices` so external
callers keep importing from the package root.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models import Device, DeploymentAssignment, FirmwareRelease


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_device(d: Device, include_secret_status: bool = True) -> dict:
    result = {
        "id": d.id,
        "display_name": d.display_name,
        "hardware_model": d.hardware_model,
        "hardware_revision": d.hardware_revision,
        "firmware_version": d.firmware_version,
        "mac_address": d.mac_address,
        "serial_number": d.serial_number,
        "local_ip": d.local_ip,
        "site_id": d.site_id,
        "registration_state": d.registration_state,
        "central_management_enabled": d.central_management_enabled,
        "capabilities": d.capabilities or {},
        "notes": d.notes,
        "last_heartbeat_at": _iso(d.last_heartbeat_at),
        # v0.6.3 (devices-page correctness): real last-contact timestamp
        # — bumped on heartbeat, the /commands long-poll, /announce, and
        # every other authenticated device path. Online/offline is
        # measured against this, not just full heartbeats.
        "last_seen_at": _iso(d.last_seen_at),
        "is_qa_fixture": bool(d.is_qa_fixture),
        "is_protected": bool(d.is_protected),
        "is_held_off": bool(d.is_held_off),
        "created_at": _iso(d.created_at),
        "updated_at": _iso(d.updated_at),
        # v0.5.52 (P0.2): device-self-reported status/recovery/central
        # truth from the last heartbeat. NULL = never reported (pre-0.1.19
        # firmware or never heartbeated). Drives the device-state chips.
        "reported_recovery_mode": d.reported_recovery_mode,
        "reported_auto_recovery_triggered": d.reported_auto_recovery_triggered,
        "reported_last_known_good_restored": d.reported_last_known_good_restored,
        "reported_consecutive_unhealthy_boots": d.reported_consecutive_unhealthy_boots,
        "reported_in_captive_portal": d.reported_in_captive_portal,
        "reported_central_enabled": d.reported_central_enabled,
        "reported_central_registered": d.reported_central_registered,
        "reported_central_state": d.reported_central_state,
    }
    if include_secret_status:
        result["device_secret_status"] = "issued"
    return result


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a possibly-naive datetime to UTC-aware (None passes
    through). Postgres returns TIMESTAMPTZ columns aware; SQLite (the
    in-process test backend) returns them naive — coerce before any
    comparison against a tz-aware `now` so the subtraction never raises.
    A no-op against a real Postgres deployment."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def effective_last_contact(
    last_heartbeat_at: datetime | None,
    last_seen_at: datetime | None,
) -> datetime | None:
    """v0.6.3 (devices-page correctness): the device's REAL last contact
    by ANY authenticated device path.

    `last_heartbeat_at` moves only on a full `/api/v1/device/heartbeat`.
    `last_seen_at` moves on every authenticated device request — the
    `/commands` long-poll, command-result, events, firmware-check,
    failsafe — and on `/announce` for an already-registered device. The
    most-recent of the two is the honest "when did we last hear from
    this device". Either may be NULL (a pre-0.6.3 row never carried
    `last_seen_at`; a never-heartbeated device has no
    `last_heartbeat_at`) — `max` over the non-NULL values, or None when
    both are absent."""
    candidates = [
        _as_utc(t) for t in (last_heartbeat_at, last_seen_at) if t is not None
    ]
    return max(candidates) if candidates else None


def _heartbeat_state_for(
    last_heartbeat_at: datetime | None,
    *,
    now: datetime,
    offline_threshold_seconds: int,
    last_seen_at: datetime | None = None,
) -> str:
    """Online/offline/never for a device.

    v0.6.3 (devices-page correctness): the freshness check is measured
    against `effective_last_contact` — the most-recent of
    `last_heartbeat_at` and `last_seen_at` — not `last_heartbeat_at`
    alone. Pre-0.6.3 a device that was actively long-polling
    `/api/v1/device/commands` (and so plainly reachable) but not yet due
    for a full heartbeat was rendered 'offline'. `last_seen_at` moves on
    every authenticated device request, so the state now reflects real
    contact. `last_seen_at` defaults to None so the legacy
    heartbeat-only behaviour is preserved for any caller that does not
    pass it.

    "never" still means the device has had NO contact at all — neither a
    heartbeat nor any other authenticated request."""
    last_contact = effective_last_contact(last_heartbeat_at, last_seen_at)
    if last_contact is None:
        return "never"
    if (now - last_contact).total_seconds() < offline_threshold_seconds:
        return "online"
    return "offline"


def _serialize_assignment(
    a: DeploymentAssignment, release: FirmwareRelease | None
) -> dict:
    return {
        "assignment_id": a.id,
        "deployment_id": a.deployment_id,
        "state": a.state,
        "target_version": release.version if release else None,
        "last_reported_version": a.last_reported_version,
        "error_message": a.error_message,
        "updated_at": _iso(a.updated_at),
    }


# v0.5.52 (P0.2): firmware `central_state` value groups, per the firmware
# status contract (docs/notes/2026-05-14-firmware-status-and-recovery-contract.md
# §1 + §4). `central_state` is a free-string device-side state machine; the
# hub maps it into operator-facing chips rather than exposing it raw.
#
# Case C — central identity needs repair / rebind.
_REBIND_CENTRAL_STATES = frozenset(
    {"registered_no_token", "awaiting_register_no_token", "reauth_required"}
)
# Case D — central client stuck retrying a transport. The device is alive
# locally but the central channel is failing.
_TRANSPORT_FAIL_CENTRAL_STATES = frozenset(
    {
        "announce_transport_failed",
        "register_transport_failed",
        "heartbeat_transport_failed",
        "poll_transport_failed",
        "firmware_check_transport_failed",
    }
)


def _derive_central_status(
    d: Device,
    *,
    heartbeat_state: str,
    latest_health_state: str | None = None,
    active_assignment: dict | None = None,
) -> dict:
    """Map (device row, heartbeat state, active firmware assignment) ->
    {code, label, reason, badge_class} for the devices list + detail UI.

    v0.5.52 (P0.2): the online/offline collapse is replaced with explicit
    states. The device-self-reported `reported_*` truth (persisted in P0.1)
    is consulted *before* heartbeat freshness — a device that disabled
    central, booted into recovery, or needs a rebind has a real reason it
    went quiet, and that reason is more actionable than a bare "offline".
    Hub-side intent (`central_management_enabled`) still wins over all of it.

    `badge_class` is one of "green" / "amber" / "red" / "" so templates can
    render a single chip without re-deriving severity. See the firmware
    status contract §4 for the case taxonomy.
    """
    # Hub opted this device out of central management entirely. Distinct
    # from the device-side `reported_central_enabled` below: this is hub
    # intent, that is the device's own local config.
    if not d.central_management_enabled:
        return {
            "code": "local_only",
            "label": "local-only",
            "reason": "Device opts out of central management.",
            "badge_class": "",
        }

    # Case A — the device's *own* config has central disabled. It may be
    # perfectly healthy and reachable on the LAN; it simply will not
    # heartbeat. This explains the silence, so it is checked before any
    # offline/stale logic. (The motivating `.69` case.)
    if d.reported_central_enabled is False:
        return {
            "code": "central_disabled",
            "label": "central disabled on device",
            "reason": (
                "The device's local config has central management turned off. "
                "It may be healthy and reachable on the LAN — re-enable central "
                "on the device to resume hub coordination."
            ),
            "badge_class": "amber",
        }

    # Case B — device booted into recovery mode. It is alive; the operator
    # action is recovery follow-up, not transport debugging.
    if d.reported_recovery_mode is True:
        boots = d.reported_consecutive_unhealthy_boots
        boot_note = (
            f" after {boots} consecutive unhealthy boots"
            if isinstance(boots, int) and boots > 1
            else ""
        )
        return {
            "code": "recovery_mode",
            "label": "recovery mode",
            "reason": (
                f"Device reported it booted into recovery mode{boot_note}. "
                "It is alive — follow up on the recovery, not transport."
            ),
            "badge_class": "amber",
        }

    # Case C — central identity needs repair (token missing / reauth).
    if d.reported_central_state in _REBIND_CENTRAL_STATES:
        return {
            "code": "rebind_needed",
            "label": "rebind needed",
            "reason": (
                f"Device central state is '{d.reported_central_state}' — its "
                "central identity needs repair or is mid-rebind."
            ),
            "badge_class": "amber",
        }

    current_version = (d.firmware_version or "").strip() or None
    target_version = (
        (active_assignment or {}).get("target_version") or ""
    ).strip() or None
    assignment_state = (active_assignment or {}).get("state")

    if target_version and current_version != target_version:
        if heartbeat_state == "offline":
            return {
                "code": "transport_stale",
                "label": "transport stale",
                "reason": (
                    f"Device is assigned {target_version} but last reported "
                    f"{current_version or 'unknown'} and is no longer heartbeating."
                ),
                "badge_class": "amber",
            }
        return {
            "code": "upgrade_pending",
            "label": "upgrade pending",
            "reason": (
                f"Device is assigned {target_version} but still reports "
                f"{current_version or 'unknown'}."
            ),
            "badge_class": "amber",
        }

    if heartbeat_state == "never":
        return {
            "code": "awaiting_first_heartbeat",
            "label": "awaiting first heartbeat",
            "reason": "Device is enrolled for central management but has not heartbeated yet.",
            "badge_class": "",
        }

    if heartbeat_state == "offline":
        # Case D — device was last seen stuck retrying a central transport.
        if d.reported_central_state in _TRANSPORT_FAIL_CENTRAL_STATES:
            return {
                "code": "transport_stale",
                "label": "transport stale",
                "reason": (
                    f"Device last reported central state '{d.reported_central_state}' "
                    "and is no longer heartbeating — the central transport is failing."
                ),
                "badge_class": "amber",
            }
        # Case E — genuinely quiet, no device-side explanation.
        return {
            "code": "central_stale",
            "label": "stale",
            "reason": "Central has not heard from this device within the heartbeat window.",
            "badge_class": "red",
        }

    if latest_health_state and latest_health_state not in ("healthy", "ok"):
        boots = d.reported_consecutive_unhealthy_boots
        boot_note = (
            f" ({boots} consecutive unhealthy boots)"
            if isinstance(boots, int) and boots > 1
            else ""
        )
        return {
            "code": "attention",
            "label": "attention",
            "reason": (
                f"Latest heartbeat reported health_state={latest_health_state}{boot_note}."
            ),
            "badge_class": "red",
        }

    if assignment_state in ("pending", "delivered") and target_version:
        return {
            "code": "upgrade_pending",
            "label": "upgrade pending",
            "reason": f"Waiting for device to report target firmware {target_version}.",
            "badge_class": "amber",
        }

    return {
        "code": "central_ok",
        "label": "central",
        "reason": "Central management is enabled and the device is reporting normally.",
        "badge_class": "green",
    }
