"""Device-announcement / pending-adoption service — v0.4.20.

The contract:
- Device boots without enrolment token, POSTs `/api/v1/device/announce`
  with `mac_address` + claims.
- Hub upserts a `device_announcements` row keyed on MAC.
- Response shape:
    pending  → {"status":"pending","retry_after_seconds":5}
    adopted  → {"status":"adopted","enrollment_token":"et_…",
               "retry_after_seconds":0,
               "central_register_url":"…/api/v1/device/register"}
    rejected → {"status":"rejected","retry_after_seconds":3600}
- v0.5.68 (P-REG fix): the `adoption_token_secret` STAYS on the row
  after delivery and is re-delivered as `{"status":"adopted"}` on
  every poll until the device registers — a device that loses one
  announce response self-heals on its next poll instead of stranding
  forever. (Pre-v0.5.68 the secret was cleared on first delivery and
  later polls got `{"status":"awaiting_register"}`; that branch is now
  reached only by the stranded-pickup recovery path.)
- Once the device registers, the announcement row's `consumed_at`
  is stamped (cross-linked from `consume_enrollment_token`) — and that
  is the only place `adoption_token_secret` is cleared.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import load_settings
from app.db import session_scope
from app.models import Device, DeviceAnnouncement, EnrollmentToken
from app.services.enrollment import _mint_enrollment_token_in_session, mint_enrollment_token


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


def _maybe_prepare_auto_rebind(*, session, row: DeviceAnnouncement, now: datetime) -> bool:
    """Best-effort self-heal path for a known device that lost its local token.

    Guardrails:
    - only when the announcement row was previously consumed (device had
      registered before),
    - only when there is an active, central-managed Device row with the same MAC,
    - only when the announcing IP still matches the hub's last-known local_ip.

    When those checks pass, mint a restore-style enrollment token targeted at
    the existing device row and reset the announcement lifecycle so the device
    can run the normal /register rebind path without operator intervention.
    """
    if row.consumed_at is None or row.adoption_token_secret:
        return False

    device = session.scalar(
        select(Device).where(
            Device.mac_address == row.mac_address,
            Device.registration_state != "decommissioned",
            Device.central_management_enabled.is_(True),
        )
    )
    if device is None:
        return False

    claimed_ip = (row.claimed_local_ip or "").strip()
    source_ip = (row.source_ip or "").strip()
    known_ip = (device.local_ip or "").strip()
    if not known_ip or (claimed_ip != known_ip and source_ip != known_ip):
        return False

    settings = load_settings()
    hint = (
        row.claimed_display_name_hint
        or device.display_name
        or f"device-{row.mac_address[-5:].replace(':','')}"
    )
    note = (
        "Auto-rebind after device-side token loss "
        f"(announcement {row.id}, device {device.id}, MAC {row.mac_address})"
    )
    record, raw_secret = _mint_enrollment_token_in_session(
        session,
        settings=settings,
        issued_by_user_id=None,
        site_id=device.site_id,
        display_name_hint=hint,
        note=note,
        ttl_seconds=86400,
        target_device_id=device.id,
    )

    # Re-enter the regular adopted -> awaiting_register lifecycle. This lets
    # the device miss one announce response without getting stranded forever in
    # "registered_no_token" again.
    row.adopted_at = now
    row.adopted_by_user_id = None
    row.adoption_token_secret = raw_secret
    row.enrollment_token_id = record.id
    row.delivered_at = None
    row.consumed_at = None
    session.flush()
    return True


def _maybe_recover_stranded_pickup(
    *, session, row: DeviceAnnouncement, now: datetime
) -> bool:
    """Recover a device stranded in the `awaiting_register` state.

    Before v0.5.68, `upsert_announcement` cleared `adoption_token_secret`
    on the *first* /announce that delivered it. A device that lost that
    single HTTP response — a dropped packet, or an ESP8266 crash under
    TLS/heap pressure — could never obtain the token again and was
    permanently stuck in `awaiting_register`. v0.5.68 stops the
    premature clear for new adoptions; this helper repairs rows that
    were *already* stranded by the old behaviour, so production devices
    bricked before the fix self-heal on their next poll with no
    operator action.

    Fires only for a row that is adopted, delivered, not consumed, not
    rejected, with the secret already gone. Re-mints a fresh enrolment
    token (carrying over the original token's site / target-device /
    name context) and re-arms the row so the normal lifecycle delivers
    it as `adopted`.
    """
    if row.adopted_at is None or row.rejected_at is not None:
        return False
    if row.adoption_token_secret or row.consumed_at is not None:
        return False
    if row.delivered_at is None:
        # Adopted but never delivered — a normal pending pickup the
        # device just hasn't polled for yet. Nothing to recover.
        return False

    old = (
        session.get(EnrollmentToken, row.enrollment_token_id)
        if row.enrollment_token_id
        else None
    )
    # If the original token was actually consumed, the device DID
    # register — `mark_consumed` just never ran (e.g. the /register
    # payload carried no MAC to cross-link on). Reconcile the row
    # instead of re-minting a token for an already-registered device.
    if old is not None and old.consumed_at is not None:
        row.consumed_at = old.consumed_at
        session.flush()
        return False

    settings = load_settings()
    site_id = old.site_id if old is not None else None
    target_device_id = (
        getattr(old, "target_device_id", None) if old is not None else None
    )
    hint = (
        (old.display_name_hint if old is not None else None)
        or row.claimed_display_name_hint
        or f"device-{row.mac_address[-5:].replace(':', '')}"
    )
    note = (
        "Re-mint after stranded awaiting_register "
        f"(announcement {row.id}, MAC {row.mac_address})"
    )
    record, raw_secret = _mint_enrollment_token_in_session(
        session,
        settings=settings,
        issued_by_user_id=None,
        site_id=site_id,
        display_name_hint=hint,
        note=note,
        ttl_seconds=86400 * 7,
        target_device_id=target_device_id,
    )
    # Re-arm the row. Reassign the FK to the new token *before* deleting
    # the old one so the announcement never references a deleted row.
    row.adoption_token_secret = raw_secret
    row.enrollment_token_id = record.id
    row.delivered_at = None
    if old is not None and old.consumed_at is None:
        # Drop the original unconsumed token — its plaintext is lost,
        # so it can never be used; leaving it would just be an orphan.
        session.delete(old)
    session.flush()
    return True


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

        _maybe_prepare_auto_rebind(session=session, row=row, now=now)
        _maybe_recover_stranded_pickup(session=session, row=row, now=now)

        # Compute the response based on lifecycle state
        if row.rejected_at is not None:
            # Rejected — back off for an hour
            return {
                "status": "rejected",
                "retry_after_seconds": 3600,
                "message": "This device was rejected by the operator. Reset device or contact admin.",
            }
        if row.adopted_at is None:
            # v0.5.10: 5s default (was 30s) — operator adoption is
            # interactive and benefits from a tight loop. Tunable via
            # REBOOTER_ANNOUNCE_PENDING_RETRY_AFTER_SECONDS.
            pending_retry = load_settings().announce_pending_retry_after_seconds
            return {
                "status": "pending",
                "retry_after_seconds": pending_retry,
                "message": "Awaiting operator adoption. Visit /app/pending-adoption.",
            }
        if row.adoption_token_secret:
            # Operator clicked Adopt — deliver the secret.
            #
            # v0.5.68 (P-REG fix): do NOT clear `adoption_token_secret`
            # here. Pre-fix it was cleared on the first /announce that
            # delivered it, so a device that lost that single HTTP
            # response — common on ESP8266 under TLS/heap pressure —
            # could never obtain the token again and was stranded
            # forever in `awaiting_register`. The secret now stays on
            # the row, re-deliverable on every poll, until the device
            # actually registers; `mark_consumed` clears it then.
            secret = row.adoption_token_secret
            if row.delivered_at is None:
                row.delivered_at = now
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
                # v0.5.68 (P-REG fix): the device has registered — the
                # plaintext enrolment token is no longer needed on the
                # row. This is now the *only* place it gets cleared.
                row.adoption_token_secret = None
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
