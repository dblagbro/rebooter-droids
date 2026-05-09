from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class UnregisteredAuthAttempt(Base):
    """Tracks heartbeat / commands calls that failed device-auth (401).

    v0.2.5: surfaces the "firmware reuses a device_id without re-running
    enrollment" failure mode that we hit on dev_01KR5EXMVJ7028D5PSAKEV6KWB
    (firmware was hard-coded to a device_id without ever calling
    /api/v1/device/register, so every heartbeat returned 401 silently in
    nginx logs).

    One row per (claimed_device_id, source_ip, endpoint). Hits are
    aggregated via UPSERT on the unique tuple.
    """

    __tablename__ = "unregistered_auth_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # device_id the caller claimed (from query/body), may be None if unparseable.
    # Capped at 80 chars to bound storage; real device_ids are ULIDs (~30 chars).
    claimed_device_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(80), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    auth_present: Mapped[bool] = mapped_column(default=False, nullable=False)

    first_seen_at: Mapped[datetime] = ts_column()
    last_seen_at: Mapped[datetime] = ts_column()
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# Composite uniqueness — one row per (claimed_device_id, source_ip, endpoint).
# claimed_device_id may be NULL; Postgres treats NULLs as distinct in UNIQUE,
# which means we'll get one row per source_ip+endpoint pair when the device_id
# is unparseable. That's fine — the table is bounded by NAT egresses, not
# device count, so it stays tiny.
UniqueConstraint(
    UnregisteredAuthAttempt.claimed_device_id,
    UnregisteredAuthAttempt.source_ip,
    UnregisteredAuthAttempt.endpoint,
    name="uq_unregistered_attempts_claim_ip_endpoint",
)
Index(
    "ix_unregistered_attempts_last_seen",
    UnregisteredAuthAttempt.last_seen_at.desc(),
)
Index(
    "ix_unregistered_attempts_claim",
    UnregisteredAuthAttempt.claimed_device_id,
)
