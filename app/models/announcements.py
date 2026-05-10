"""Pending-adoption announcements — v0.4.20.

A device that boots without an enrolment token can announce itself
to the hub via `POST /api/v1/device/announce` (unauthenticated).
The hub records the announcement, the operator sees it on
`/app/pending-adoption`, clicks **Adopt**, and a fresh enrolment
token is delivered back to the device on its next announce poll.
The device then runs the normal `/register` flow.

This replaces the old "mint a token in the UI, paste into firmware
build at flash time" workflow — devices come up automatically and
get adopted by name.

Keyed by MAC. A device announcing repeatedly with the same MAC
just bumps `last_seen_at` + `announce_count`. A factory-reset
that wipes the local config will produce a new announcement
(its consumed_at on the old row remains as audit history).
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class DeviceAnnouncement(Base):
    __tablename__ = "device_announcements"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "ann")
    )

    # Primary identifier — the device's MAC. Uniqueness lets a
    # device announce repeatedly without creating duplicate rows.
    # We don't FK to `devices` because adoption may not have
    # happened yet (and may never happen — operator can reject).
    mac_address: Mapped[str] = mapped_column(
        String(40), nullable=False, unique=True
    )

    # Caller-supplied claims. Validated at the same column-width
    # bounds as `consume_enrollment_token` (BUG-050 family).
    claimed_hardware_model: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    claimed_hardware_revision: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    claimed_firmware_version: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    claimed_local_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    claimed_serial_number: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    # Optional human-readable hint the operator can use as the
    # device's display_name on adopt; otherwise display_name
    # falls back to "device-<last 4 of MAC>".
    claimed_display_name_hint: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )

    # Request metadata captured server-side
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Lifecycle timestamps
    first_seen_at: Mapped[datetime] = ts_column()
    last_seen_at: Mapped[datetime] = ts_column()
    announce_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )

    # Adoption state
    # adopted_at NULL                                  → pending
    # adopted_at set, delivered_at NULL                → operator clicked Adopt;
    #                                                    waiting for next announce poll
    # delivered_at set, consumed_at NULL               → token returned to device;
    #                                                    waiting for /register
    # consumed_at set                                  → device registered → success
    adopted_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    adopted_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The raw enrolment-token secret, plaintext, cleared after
    # delivery. Never returned to admin queries — only to the
    # device that announced it.
    adoption_token_secret: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    # The enrolment-token row id we minted at adoption time, kept
    # so the operator can audit the link between announcement and
    # its consumed token.
    enrollment_token_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("enrollment_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    consumed_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )

    # If the operator rejects, soft-delete with a timestamp + reason.
    # A re-announce from the same MAC after a reject will create a new
    # row with a new id (we delete the old one on reject).
    rejected_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )


Index(
    "ix_device_announcements_state",
    DeviceAnnouncement.adopted_at, DeviceAnnouncement.consumed_at,
)
Index(
    "ix_device_announcements_last_seen",
    DeviceAnnouncement.last_seen_at.desc(),
)
