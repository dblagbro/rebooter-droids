from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "usr")
    )
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_super_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()
    last_login_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
