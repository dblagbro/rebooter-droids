from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "site")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()
