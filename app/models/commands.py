from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column

COMMAND_TYPES = (
    "relay_on",
    "relay_off",
    "relay_toggle",
    "relay_cycle",
    "device_restart",
    "factory_reset",
    "set_mode",
    "apply_config",
    "check_firmware",
    "start_firmware_update",
)

COMMAND_STATUSES = ("pending", "accepted", "running", "completed", "failed", "expired")


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "cmd")
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    issued_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    created_at: Mapped[datetime] = ts_column()
    expires_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    delivered_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    completed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)


Index(
    "ix_commands_device_status_expires",
    Command.device_id,
    Command.status,
    Command.expires_at,
)


class CommandResult(Base):
    __tablename__ = "command_results"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "cmr")
    )
    command_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("commands.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    received_at: Mapped[datetime] = ts_column()
