from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device, DeviceHeartbeat
from app.services.deployments import reconcile_assignment_reported_version

# v0.5.51 (P0.1): firmware status/recovery/central fields the heartbeat
# now carries (firmware 0.1.19-dev-central-safe+). Every field is copied
# verbatim onto the DeviceHeartbeat history row.
_HEARTBEAT_STATUS_FIELDS = (
    "recovery_mode",
    "auto_recovery_triggered",
    "last_known_good_restored",
    "consecutive_unhealthy_boots",
    "in_captive_portal",
    "holdoff_remaining_seconds",
    "cooldown_remaining_seconds",
    "central_enabled",
    "central_registered",
    "central_state",
    "central_device_id",
    "central_heartbeat_age_seconds",
    "power_analytics_enabled",
    "power_chip_type",
    "power_sample_rate_hz",
    "power_batch_seconds",
)

# Subset that is also mirrored onto Device as `reported_<field>` hot
# columns — the current-truth fields P0.2 maps into state chips. Only
# refreshed when the device actually reports the field, so a partial
# payload (or pre-0.1.19 firmware) never clobbers last-known truth.
_DEVICE_HOT_FIELDS = (
    "recovery_mode",
    "auto_recovery_triggered",
    "last_known_good_restored",
    "consecutive_unhealthy_boots",
    "in_captive_portal",
    "central_enabled",
    "central_registered",
    "central_state",
)


def record_heartbeat(device_id: str, payload: dict) -> dict:
    now = datetime.now(timezone.utc)
    last_event_at = payload.get("last_event_at")
    last_event_dt = None
    if last_event_at:
        try:
            last_event_dt = datetime.fromisoformat(last_event_at.replace("Z", "+00:00"))
        except ValueError:
            last_event_dt = None

    with session_scope() as session:
        device = session.get(Device, device_id)
        if device is None:
            raise LookupError(device_id)

        device.last_heartbeat_at = now
        # v0.6.3 (devices-page correctness): a full heartbeat is also
        # contact — keep `last_seen_at` (the real last-contact timestamp
        # the devices list measures online/offline against) in step. The
        # device-auth middleware already stamps it for every
        # authenticated request, but `record_heartbeat` opens its own
        # session, so set it here too rather than depending on write
        # ordering between the two sessions.
        device.last_seen_at = now
        if payload.get("firmware_version"):
            device.firmware_version = payload["firmware_version"]
        if payload.get("local_ip"):
            device.local_ip = payload["local_ip"]
        session.add(device)

        hb = DeviceHeartbeat(
            device_id=device_id,
            received_at=now,
            firmware_version=payload.get("firmware_version"),
            local_ip=payload.get("local_ip"),
            mode=payload.get("mode"),
            relay_on=payload.get("relay_on"),
            wifi_connected=payload.get("wifi_connected"),
            health_state=payload.get("health_state"),
            uptime_seconds=payload.get("uptime_seconds"),
            incident_cycles=payload.get("incident_cycles"),
            hour_cycles=payload.get("hour_cycles"),
            last_event_type=payload.get("last_event_type"),
            last_event_at=last_event_dt,
        )
        # v0.5.51 (P0.1): copy the richer firmware status/recovery/central
        # fields onto the history row. Missing keys land NULL — that heartbeat
        # simply didn't carry the field (older firmware or partial payload).
        for field in _HEARTBEAT_STATUS_FIELDS:
            if field in payload:
                setattr(hb, field, payload[field])
        session.add(hb)

        # v0.5.53 (P0.3 / Phase 4B): capture the pre-update recovery truth
        # so we can detect a transition once the hot columns are refreshed.
        prev_recovery_mode = device.reported_recovery_mode
        prev_lkg_restored = device.reported_last_known_good_restored

        # Refresh the Device hot columns for current-truth filtering. Only
        # touch a column when the device actually reported it, so a partial
        # payload never overwrites last-known state with NULL.
        for field in _DEVICE_HOT_FIELDS:
            if field in payload:
                setattr(device, f"reported_{field}", payload[field])

        # Detect a recovery transition. `last_known_good_restored` newly
        # going true means the device just re-applied last-known-good
        # config; exiting recovery mode (true -> false) means a recovery
        # incident just closed. Either can leave the on-box config diverged
        # from operator intent — Phase 4B re-asserts desired_config.
        recovery_trigger: str | None = None
        if (
            prev_lkg_restored is not True
            and device.reported_last_known_good_restored is True
        ):
            recovery_trigger = "last_known_good_restored"
        elif (
            prev_recovery_mode is True
            and device.reported_recovery_mode is False
        ):
            recovery_trigger = "recovery_exit"
        reconcile_assignment_reported_version(
            session,
            device_id,
            payload.get("firmware_version"),
            error_message=payload.get("health_state"),
            reported_at=now,
        )
        # v0.5.22 (B21): firmware can echo its current config in the
        # heartbeat under `reported_config`. Stash it on the row so
        # drift detection has a current snapshot. Today only
        # `device_name` is reliably populated by the firmware; other
        # keys land as the firmware-team grows apply_config schema
        # support per `docs/firmware-apply-config-schema-v01.md`.
        reported_cfg = payload.get("reported_config")
        if isinstance(reported_cfg, dict):
            device.last_reported_config = reported_cfg
            session.add(device)

        # Tier-2: the firmware retires the dedicated
        # /device/power-samples endpoint and folds a compact `power`
        # summary object (min/avg/max W, latest V/A/PF, energy Wh, frame
        # counts) into the heartbeat. Store it as a `source="heartbeat"`
        # DevicePowerSample row in the same session — no extra round-trip.
        # Best-effort: a malformed `power` object must never block the
        # heartbeat, so the ingest is wrapped + the call is no-op'd on a
        # bad shape rather than raising.
        power_summary = payload.get("power")
        if isinstance(power_summary, dict):
            from app.services.events import ingest_power_summary

            try:
                ingest_power_summary(session, device_id, power_summary, now)
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "heartbeat power-summary ingest failed for %s", device_id
                )

        session.flush()

    # v0.5.53 (P0.3 / Phase 4B): after the heartbeat commits, re-assert
    # desired_config if the device just came through a recovery transition.
    # Deferred import — device_config transitively pulls in the commands +
    # audit services. Best-effort: maybe_push_after_recovery never raises.
    if recovery_trigger is not None:
        from app.services.device_config import maybe_push_after_recovery

        maybe_push_after_recovery(device_id, trigger=recovery_trigger)

    return {"recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


def latest_heartbeat(session, device_id: str) -> DeviceHeartbeat | None:
    return session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == device_id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
