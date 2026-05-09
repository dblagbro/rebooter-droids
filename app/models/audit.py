from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = ts_column()

    # actor_user_id is nullable — device-API actions can land here too.
    actor_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email_snapshot: Mapped[str | None] = mapped_column(String(254), nullable=True)

    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


Index("ix_audit_at_desc", AuditEvent.at.desc())
Index("ix_audit_actor", AuditEvent.actor_user_id)
Index("ix_audit_action", AuditEvent.action)
Index("ix_audit_target", AuditEvent.target_type, AuditEvent.target_id)
