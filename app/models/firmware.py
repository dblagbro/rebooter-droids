from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column

FIRMWARE_CHANNELS = ("dev", "beta", "stable")
DEPLOYMENT_TARGET_TYPES = ("device", "group", "site", "all_devices")
ASSIGNMENT_STATES = ("pending", "delivered", "completed", "failed", "superseded")


class FirmwareRelease(Base):
    __tablename__ = "firmware_releases"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "fwr")
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="dev")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    download_url: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        UniqueConstraint("version", "channel", name="uq_firmware_version_channel"),
    )


class FirmwareDeployment(Base):
    __tablename__ = "firmware_deployments"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "fwd")
    )
    release_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("firmware_releases.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="dev")
    force: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    issued_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = ts_column()


class DeploymentAssignment(Base):
    __tablename__ = "deployment_assignments"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "fda")
    )
    deployment_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("firmware_deployments.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("firmware_releases.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    last_reported_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()


Index(
    "ix_assignment_device_state",
    DeploymentAssignment.device_id,
    DeploymentAssignment.state,
)
