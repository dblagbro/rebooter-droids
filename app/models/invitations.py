from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "inv")
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    issued_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    expires_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    consumed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    consumed_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = ts_column()


Index("ix_invitations_email", Invitation.email)
Index("ix_invitations_expires_at", Invitation.expires_at)
