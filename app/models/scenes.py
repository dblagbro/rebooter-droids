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

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "scn")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # items: [{"device_id": str, "relay": "on"|"off"|"cycle", "config": {…}}]
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()
