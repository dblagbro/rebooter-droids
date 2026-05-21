from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped, tenant_scoped_org_column


class Site(TenantScoped, Base):
    # org-boundary phase 3: `Site` is the pivot — org owns sites.
    # `organization_id` is NOT NULL with an on-delete RESTRICT FK
    # (migration 0005), and `name` is unique *per org* —
    # UNIQUE(organization_id, name) — not globally. See design §2, §6.3.
    __tablename__ = "sites"

    organization_id = tenant_scoped_org_column("RESTRICT")

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "site")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_sites_org_name"),
    )
