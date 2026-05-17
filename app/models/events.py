from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class DeviceEvent(Base):
    __tablename__ = "device_events"

    # BigInteger on Postgres; Integer on SQLite so the PK autoincrements
    # under in-process tests (SQLite only auto-rowids an INTEGER PK).
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    timestamp: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    received_at: Mapped[datetime] = ts_column()
    mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


Index(
    "ix_device_events_device_timestamp",
    DeviceEvent.device_id,
    DeviceEvent.timestamp.desc(),
)
Index("ix_device_events_type", DeviceEvent.type)
