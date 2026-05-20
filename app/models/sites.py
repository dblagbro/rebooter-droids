from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped


class Site(TenantScoped, Base):
    # TODO(org-phase3): `Site` is the pivot — org owns sites. The phase-2
    # migration flips `organization_id` to NOT NULL with an on-delete
    # RESTRICT FK and swaps `name`'s global unique for a per-org
    # UNIQUE(organization_id, name). See design §2, §6.3.
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "site")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()
