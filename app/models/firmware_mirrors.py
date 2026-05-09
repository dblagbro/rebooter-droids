"""Firmware-release mirror records — v0.3.9 (RFC-002 P1).

Each FirmwareRelease can be published to multiple mirrors (the
canonical local hosting under /rebooter/firmware/<channel>/, plus
GitHub Releases as the operationally-independent fallback once
the v0.3.10 GitHub publisher ships, plus optionally jsDelivr per
RFC-002 §7).

This table is the per-(release, mirror-kind) row that records the
URL, status, and verification metadata. Cascade-deletes with the
parent release.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


# Per RFC-002 §7.1 — keep narrow in v1, can extend later.
MIRROR_KIND_LOCAL = "local"
MIRROR_KIND_GITHUB_RELEASE = "github_release"
MIRROR_KIND_JSDELIVR = "jsdelivr_gh"
MIRROR_KIND_OBJECT_STORAGE = "object_storage"

KNOWN_MIRROR_KINDS = (
    MIRROR_KIND_LOCAL,
    MIRROR_KIND_GITHUB_RELEASE,
    MIRROR_KIND_JSDELIVR,
    MIRROR_KIND_OBJECT_STORAGE,
)

# Per RFC-002 §7.1
MIRROR_STATUS_PENDING = "pending"
MIRROR_STATUS_LIVE = "live"
MIRROR_STATUS_FAILED = "failed"


class FirmwareReleaseMirror(Base):
    __tablename__ = "firmware_release_mirrors"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "fmir")
    )
    release_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("firmware_releases.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MIRROR_STATUS_PENDING
    )

    # SHA-256 of the bytes the mirror serves, post-publish probe.
    # NULL until the GitHub publisher (v0.3.10 P2) probes.
    verified_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_probed_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = ts_column()


Index(
    "ix_firmware_release_mirrors_release",
    FirmwareReleaseMirror.release_id,
    FirmwareReleaseMirror.kind,
    unique=True,
)
