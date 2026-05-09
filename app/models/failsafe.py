"""Device-side failsafe-event log — v0.3.8 (RFC-005 P1).

When a device falls back from slot B (just-OTA'd main firmware)
to slot C (last-known-good main firmware) it POSTs to
/api/v1/device/failsafe and we record the event here. The
operator sees these on the Status inbox as a high-severity
attention item plus on the per-device Audit/Events tab.

Distinct from `device_events` (firmware-emitted operational
events) and `audit_events` (admin actions); failsafe is a narrow,
structured signal "this version did not boot for me, I am
running the previous version."
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


# Documented reason values. The endpoint accepts these (and
# anything else the firmware sends; unknown reasons are stored
# verbatim and surface in the diagnostic UI for forensics).
FAILSAFE_REASON_BOOT_FAILURE = "boot_failure"
FAILSAFE_REASON_SHA256_MISMATCH = "sha256_mismatch"
FAILSAFE_REASON_WATCHDOG_RESET = "watchdog_reset"
FAILSAFE_REASON_TIMEOUT = "timeout"
FAILSAFE_REASON_OTHER = "other"

KNOWN_FAILSAFE_REASONS = (
    FAILSAFE_REASON_BOOT_FAILURE,
    FAILSAFE_REASON_SHA256_MISMATCH,
    FAILSAFE_REASON_WATCHDOG_RESET,
    FAILSAFE_REASON_TIMEOUT,
    FAILSAFE_REASON_OTHER,
)


class DeviceFailsafeEvent(Base):
    __tablename__ = "device_failsafe_events"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "fse")
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )

    received_at: Mapped[datetime] = ts_column()

    # The version the device tried to run and fell back FROM.
    failed_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The version the device successfully fell back TO (slot C).
    fallback_to_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # One of KNOWN_FAILSAFE_REASONS or a free-form value the firmware
    # sent. We store both in case the firmware extends the vocabulary.
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="other")

    # Diagnostic blob — firmware-team-defined; kept opaque on the
    # backend side so future firmware additions don't require a
    # schema migration on every change.
    details: Mapped[str | None] = mapped_column(Text, nullable=True)


Index(
    "ix_device_failsafe_events_device_received",
    DeviceFailsafeEvent.device_id,
    DeviceFailsafeEvent.received_at.desc(),
)
