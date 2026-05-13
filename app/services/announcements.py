"""Device-announcement / pending-adoption service — v0.4.20.

The contract:
- Device boots without enrolment token, POSTs `/api/v1/device/announce`
  with `mac_address` + claims.
- Hub upserts a `device_announcements` row keyed on MAC.
- Response shape:
    pending  → {"status":"pending","retry_after_seconds":30}
    adopted  → {"status":"adopted","enrollment_token":"et_…",
               "retry_after_seconds":0,
               "central_register_url":"…/api/v1/device/register"}
    rejected → {"status":"rejected","retry_after_seconds":3600}
- After delivery the `adoption_token_secret` is cleared from the
  row so the plaintext doesn't sit around. Subsequent polls before
  the device successfully registers return
  `{"status":"awaiting_register"}` (token's already been handed out).
- Once the device registers, the announcement row's `consumed_at`
  is stamped (cross-linked from the `consume_enrollment_token`
  service path).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import load_settings
from app.db import session_scope
from app.models import DeviceAnnouncement
from app.services.enrollment import mint_enrollment_token


# Same column-width caps as `consume_enrollment_token` (BUG-050).
_FIELD_CAPS: dict[str, int] = {
    "claimed_hardware_model": 80,
    "claimed_hardware_revision": 40,
    "claimed_firmware_version": 40,
    "claimed_local_ip": 64,
    "claimed_serial_number": 80,
    "claimed_display_name_hint": 120,
}
_MAC_RE = re.compile(r"[0-9A-Fa-f:.\-\s]+")


class AnnouncementError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ── public-side: announce ────────────────────────────────────────────

def upsert_announcement(
    *,
    mac_address: str,
    hardware_model: str | None = None,
    hardware_revision: str | None = None,
    firmware_version: str | None = None,
    local_ip: str | None = None,
    serial_number: str | None = None,
    display_name_hint: str | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Returns a dict with the response payload for the device:
    `{status, retry_after_seconds, ...}`. Lazily upserts the row.
    """
    mac = (mac_address or "").strip()
    if not mac:
        raise AnnouncementError("validation_failed", "mac_address is required")
    if len(mac) > 40:
        raise AnnouncementError(
            "validation_failed", "mac_address must be 40 characters or fewer"
        )
    if not _MAC_RE.fullmatch(mac):
        raise AnnouncementError(
            "validation_failed",
            "mac_address must contain only hex digits, ':', '-', '.', or spaces",
        )

    claims = {
        "claimed_hardware_model": hardware_model,
        "claimed_hardware_revision": hardware_revision,
        "claimed_firmware_version": firmware_version,
        "claimed_local_ip": local_ip,
        "claimed_serial_number": serial_number,
        "claimed_display_name_hint": display_name_hint,
    }
    for field, value in claims.items():
        if value is not None and not isinstance(value, str):
            raise AnnouncementError("validation_failed", f"{field} must be a string")
        if value and len(value) > _FIELD_CAPS[field]:
            raise AnnouncementError(
                "validation_failed",
                f"{field} must be {_FIELD_CAPS[field]} characters or fewer",
            )

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.scalar(
            select(DeviceAnnouncement).where(
                DeviceAnnouncement.mac_address == mac
            )
        )
        if row is None:
            row = DeviceAnnouncement(
                mac_address=mac,
                first_seen_at=now,
                last_seen_at=now,
                announce_count=1,
                source_ip=source_ip,
                user_agent=(user_agent[:200] if user_agent else None),
                **{k: v for k, v in claims.items() if v},
            )
            session.add(row)
        else:
            row.last_seen_at = now
            row.announce_count = (row.announce_count or 0) + 1
            row.source_ip = source_ip
            if user_agent:
                row.user_agent = user_agent[:200]
            # Refresh claim fields if the device reports new ones (e.g.,
            # firmware_version bumps after an OTA before adoption).
            for k, v in claims.items():
                if v:
                    setattr(row, k, v)

        session.flush()

        # Compute the response based on lifecycle state
        if row.rejected_at is not None:
            # Rejected — back off for an hour
            return {
                "status": "rejected",
                "retry_after_seconds": 3600,
                "message": "This device was rejected by the operator. Reset device or contact admin.",
            }
        if row.adopted_at is None:
            return {
                "status": "pending",
                "retry_after_seconds": 30,
                "message": "Awaiting operator adoption. Visit /app/pending-adoption.",
            }
        if row.adoption_token_secret:
            # Operator clicked Adopt — deliver the secret + mark delivered
            secret = row.adoption_token_secret
            row.delivered_at = now
            row.adoption_token_secret = None
            session.flush()
            settings = load_settings()
            return {
                "status": "adopted",
                "retry_after_seconds": 0,
                "enrollment_token": secret,
                "central_register_url": (
                    settings.public_base_url.rstrip("/") + "/api/v1/device/register"
                ),
                "message": "Adopted. Use this token to register.",
            }
        if row.consumed_at is not None:
            return {
                "status": "registered",
                "retry_after_seconds": 0,
                "message": "Device already registered. Use device_token, not enrollment_token.",
            }
        # delivered_at set but consumed_at not yet → device got the
        # secret but hasn't completed /register yet
        return {
            "status": "awaiting_register",
            "retry_after_seconds": 60,
            "message": "Token already delivered. Complete /register.",
        }


# ── operator-side: list, adopt, reject ───────────────────────────────


def serialize(row: DeviceAnnouncement) -> dict:
    """Operator-facing — never includes `adoption_token_secret`."""
    state = "pending"
    if row.rejected_at is not None:
        state = "rejected"
    elif row.consumed_at is not None:
        state = "registered"
    elif row.delivered_at is not None:
        state = "awaiting_register"
    elif row.adopted_at is not None:
        state = "awaiting_pickup"
    return {
        "id": row.id,
        "mac_address": row.mac_address,
        "claimed_hardware_model": row.claimed_hardware_model,
        "claimed_hardware_revision": row.claimed_hardware_revision,
        "claimed_firmware_version": row.claimed_firmware_version,
        "claimed_local_ip": row.claimed_local_ip,
        "claimed_serial_number": row.claimed_serial_number,
        "claimed_display_name_hint": row.claimed_display_name_hint,
        "source_ip": row.source_ip,
        "user_agent": row.user_agent,
        "first_seen_at": _iso(row.first_seen_at),
        "last_seen_at": _iso(row.last_seen_at),
        "announce_count": row.announce_count,
        "state": state,
        "adopted_at": _iso(row.adopted_at),
        "delivered_at": _iso(row.delivered_at),
        "consumed_at": _iso(row.consumed_at),
        "rejected_at": _iso(row.rejected_at),
        "enrollment_token_id": row.enrollment_token_id,
    }


def list_announcements(*, include_consumed: bool = False) -> list[dict]:
    with session_scope() as session:
        stmt = select(DeviceAnnouncement).order_by(
            DeviceAnnouncement.last_seen_at.desc()
        )
        rows = list(session.scalars(stmt))
        if not include_consumed:
            rows = [r for r in rows if r.consumed_at is None and r.rejected_at is None]
        return [serialize(r) for r in rows]


def count_pending_announcements() -> int:
    """v0.5.2: cheap pending-adoption count for the devices-list
    sub-header. Counts rows with consumed_at IS NULL AND
    rejected_at IS NULL — the same predicate `list_announcements`
    uses by default. Done as a single SELECT COUNT(*) to avoid
    serializing the full rows just to render a badge.
    """
    from sqlalchemy import func, select as _select
    with session_scope() as session:
        return session.scalar(
            _select(func.count(DeviceAnnouncement.id)).where(
                DeviceAnnouncement.consumed_at.is_(None),
                DeviceAnnouncement.rejected_at.is_(None),
            )
        ) or 0


def adopt(announcement_id: str, *, by_user_id: str | None,
          display_name: str | None = None,
          mode: str = "fresh",
          target_device_id: str | None = None) -> dict:
    """Operator action: mint a fresh enrolment token and stash it
    on the announcement row. Returns the serialized announcement
    (no secret). Idempotent — adopting an already-adopted row
    returns 200 with the existing state (no second token mint).

    `display_name` overrides the claimed hint; defaults to the
    hint, or `device-<last 4 of MAC>` if nothing else.

    v0.5.7 (B20): `mode` and `target_device_id` add the
    restore-after-reflash flow.
      - mode='fresh' (default): today's behaviour. Mints a token
        that /device/register will use to create a brand-new
        Device row.
      - mode='restore': requires `target_device_id`. Mints a token
        with `target_device_id` set; /device/register rebinds the
        existing Device row's credentials instead of creating a
        new one. Caller must have verified that the existing
        device's MAC matches the announcement's MAC (the UI does
        this; /device/register also checks defensively).
    """
    if mode not in ("fresh", "restore"):
        raise AnnouncementError("validation_failed", f"unknown adopt mode: {mode}")
    if mode == "restore" and not target_device_id:
        raise AnnouncementError("validation_failed", "restore mode requires target_device_id")

    settings = load_settings()
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(DeviceAnnouncement, announcement_id)
        if row is None:
            raise AnnouncementError("not_found", "Announcement not found")
        if row.rejected_at is not None:
            raise AnnouncementError(
                "rejected", "Announcement was rejected; cannot adopt"
            )
        if row.adopted_at is not None:
            # Idempotent — already adopted
            return serialize(row)

        # Defensive MAC-match check for restore mode (UI should have
        # filtered to matching devices already, but trust nothing).
        if mode == "restore":
            from app.models import Device
            existing = session.get(Device, target_device_id)
            if existing is None:
                raise AnnouncementError(
                    "not_found", f"target device {target_device_id} not found"
                )
            if existing.registration_state == "decommissioned":
                raise AnnouncementError(
                    "validation_failed",
                    "target device is decommissioned; cannot restore",
                )
            ann_mac = (row.mac_address or "").strip().upper()
            dev_mac = (existing.mac_address or "").strip().upper()
            if ann_mac and dev_mac and ann_mac != dev_mac:
                raise AnnouncementError(
                    "validation_failed",
                    "MAC mismatch between announcement and target device",
                )

        hint = (
            display_name
            or row.claimed_display_name_hint
            or f"device-{row.mac_address[-5:].replace(':','')}"
        )
        note = (
            f"Adopted via /app/pending-adoption (announcement {row.id}, MAC {row.mac_address})"
            if mode == "fresh"
            else f"Restored to existing device {target_device_id} via /app/pending-adoption (announcement {row.id}, MAC {row.mac_address})"
        )
        record, raw_secret = mint_enrollment_token(
            settings,
            issued_by_user_id=by_user_id,
            display_name_hint=hint,
            note=note,
            ttl_seconds=86400 * 7,  # 7-day TTL — plenty for the device's next poll
            target_device_id=target_device_id if mode == "restore" else None,
        )
        row.adopted_at = now
        row.adopted_by_user_id = by_user_id
        row.adoption_token_secret = raw_secret
        row.enrollment_token_id = record.id
        session.flush()
        return serialize(row)


def reject(announcement_id: str, *, by_user_id: str | None) -> dict | None:
    """Operator action: mark the announcement rejected. Subsequent
    /announce calls from the same MAC will get a `rejected` response
    with a 1-hour back-off. Returns the serialized row, or None if
    not found."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(DeviceAnnouncement, announcement_id)
        if row is None:
            return None
        if row.rejected_at is None:
            row.rejected_at = now
        # Note: we keep `adopted_by_user_id` for the rejection actor
        # too — same column, dual-purpose. Could split if needed.
        if by_user_id and row.adopted_by_user_id is None:
            row.adopted_by_user_id = by_user_id
        session.flush()
        return serialize(row)


def mark_consumed(mac_address: str) -> None:
    """Called from `consume_enrollment_token` after a /register
    succeeds. Looks up the announcement by MAC and stamps
    `consumed_at` if present. Best-effort — never raises."""
    if not mac_address:
        return
    try:
        with session_scope() as session:
            row = session.scalar(
                select(DeviceAnnouncement).where(
                    DeviceAnnouncement.mac_address == mac_address
                )
            )
            if row is not None and row.consumed_at is None:
                row.consumed_at = datetime.now(timezone.utc)
                session.flush()
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "mark_consumed failed for mac=%s", mac_address
        )


def delete(announcement_id: str) -> bool:
    with session_scope() as session:
        row = session.get(DeviceAnnouncement, announcement_id)
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True
