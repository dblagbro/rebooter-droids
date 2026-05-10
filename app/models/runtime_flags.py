"""Runtime flags — singleton-ish key/value store.

v0.4.7 introduces this for the portal-wide `maintenance_mode_active`
flag (B7). The operator toggles it from the UI; the watchdog tick
reads it on every cycle and short-circuits if true.

Only used for state the operator must change without a redeploy.
Anything that's set-and-forget belongs in env vars (see
`app/config.py`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class RuntimeFlag(Base):
    __tablename__ = "runtime_flags"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = ts_column()
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
