from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column
from app.services.tenant_scope import TenantScoped


class AuditEvent(TenantScoped, Base):
    # TODO(org-phase2): `organization_id` stays NULLABLE even after
    # phase 2 — platform/system audit rows (RBAC backfills, etc.) have no
    # org. It is stamped from the active org at `audit.record()` time and
    # platform-NULL rows are visible only to platform staff. See
    # design §3.6. `AuditEventArchive` mirrors this — see below.
    __tablename__ = "audit_events"

    # BigInteger on Postgres; Integer on SQLite so the PK autoincrements
    # under in-process tests (SQLite only auto-rowids an INTEGER PK).
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
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


# v0.5.36 (B1 RBAC P2): archive table for soft-pruned audit events.
# Mirrors AuditEvent shape with an additional archived_at timestamp.
class AuditEventArchive(Base):
    __tablename__ = "audit_events_archive"

    # BigInteger on Postgres; Integer on SQLite — keeps the archive
    # table's schema consistent with AuditEvent above. Archive rows are
    # inserted with an explicit id copied from the source event.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    at: Mapped[datetime] = ts_column(default_now=False, nullable=False)

    actor_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_email_snapshot: Mapped[str | None] = mapped_column(String(254), nullable=True)

    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Mirror of AuditEvent.organization_id so the prune copy stays
    # complete. A plain nullable column — NOT the TenantScoped mixin: the
    # archive is not a phase-2 query-filter target (only `audit_events`
    # is Tier-A, design §3.6). Stays nullable forever — platform/system
    # audit rows have no org.
    # TODO(org-phase2): keep this in sync with AuditEvent.organization_id.
    organization_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    archived_at: Mapped[datetime] = ts_column()


Index("ix_audit_archive_at_desc", AuditEventArchive.at.desc())
Index("ix_audit_archive_archived_at", AuditEventArchive.archived_at)
