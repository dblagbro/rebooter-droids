from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped, tenant_scoped_org_column


class Invitation(TenantScoped, Base):
    # org-boundary phase 3: `organization_id` is NOT NULL — an invite is
    # always into one org (design §5.2) — with an on-delete CASCADE FK
    # (an invite is meaningless once its org is gone). Migration 0005.
    __tablename__ = "invitations"

    organization_id = tenant_scoped_org_column("CASCADE")

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

    # v0.5.38 (B1 RBAC P4): scope bindings to grant on redemption.
    # Shape: {"bindings": [{"scope_type": "site", "scope_id": "site_01..."}, ...]}
    # NULL = legacy behavior (global role only, no bindings).
    scope_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    expires_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    consumed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    consumed_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = ts_column()


Index("ix_invitations_email", Invitation.email)
Index("ix_invitations_expires_at", Invitation.expires_at)
