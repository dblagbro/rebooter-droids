from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped, tenant_scoped_org_column


class Group(TenantScoped, Base):
    # org-boundary phase 3: `organization_id` is NOT NULL with an
    # on-delete RESTRICT FK (migration 0005), and `name` is unique
    # *per org* — UNIQUE(organization_id, name) — not globally.
    # See design §2, §6.3.
    __tablename__ = "groups"

    organization_id = tenant_scoped_org_column("RESTRICT")

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "grp")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_groups_org_name"),
    )


class GroupMembership(Base):
    __tablename__ = "group_memberships"

    group_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        UniqueConstraint("group_id", "device_id", name="uq_group_membership"),
    )
