"""Runtime settings — key/value store for operator-editable config.

v0.4.25 introduces this for SMTP creds; v0.4.26 extends to Network +
System settings. Distinct from `runtime_flags` (v0.4.7), which is
for boolean toggles like maintenance_mode_active. This is for the
broader "I don't want to recreate the container to rotate SMTP
password" use case.

Reads always go DB-first → env-var fallback. So an empty DB on a
fresh deployment still picks up sane env-var defaults; once the
operator edits via the UI, the DB row wins.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    # Dotted key, e.g. `smtp.host`, `network.cors_allowed_origins`.
    name: Mapped[str] = mapped_column(String(80), primary_key=True)

    # JSON-typed value. Strings, ints, lists — caller decides shape.
    # Always {"v": <value>} so we can introspect actual nulls vs
    # absent keys without column-shape juggling.
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    updated_at: Mapped[datetime] = ts_column()
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
