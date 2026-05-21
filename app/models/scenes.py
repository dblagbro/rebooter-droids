"""Named device scenes — v0.5.92 (Stage C).

A `Scene` is a reusable, named bundle of per-device target states: each
`items` entry sets one device to a relay state (`on` / `off` / `cycle`)
and/or pushes an `apply_config` payload. A watchdog `apply_scene` action
references a scene by `scene_id` — so "Erica's TV audio" can be authored
once and reused across rules — or carries the items inline.

New table: `Base.metadata.create_all()` at startup adds it on every
deployment; no `_PENDING_COLUMNS` ALTER needed.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped, tenant_scoped_org_column


class Scene(TenantScoped, Base):
    # org-boundary phase 3: `organization_id` is NOT NULL with an
    # on-delete RESTRICT FK (migration 0005), and `name` is unique
    # *per org* — UNIQUE(organization_id, name) — not globally.
    # See design §2, §6.3.
    __tablename__ = "scenes"

    organization_id = tenant_scoped_org_column("RESTRICT")

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "scn")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # items: [{"device_id": str, "relay": "on"|"off"|"cycle", "config": {…}}]
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_scenes_org_name"),
    )
