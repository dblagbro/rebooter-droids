"""Schedules — v0.4.8 (B8).

Time-driven counterpart to watchdog rules. Rules fire on probe
failure; **schedules fire on time**.

Two kinds shipped in v0.4.8:

  - `power_cycle`  — fire `relay_cycle` against a target on a cron
                     schedule. Replaces ad-hoc operator
                     "right-click → cycle this group at 3am" workflows.
  - `maintenance`  — flip portal-wide maintenance ON for a
                     window, OFF after. Lets the operator schedule
                     "every Saturday 2-3am, suppress watchdog rules".

Schedules are interval-based (cron-shaped strings) but v0.4.8 only
supports daily / weekly / one-shot via three discrete fields:

  - `recurrence`   — 'once' | 'daily' | 'weekly'
  - `at_time_utc`  — 'HH:MM' (UTC)
  - `weekdays`     — array of 0-6 (Mon=0..Sun=6) for 'weekly'
  - `start_at`     — ISO datetime, only for 'once'
  - `duration_seconds` — how long to hold maintenance on (0 for power_cycle)

The runtime tick reads schedules and:
  - For `power_cycle` schedules: fires the cycle command if `now` >=
    next_run_at AND was not fired in the last 60 s.
  - For `maintenance` schedules: flips maintenance_mode_active when
    in-window; flips OFF when the window ends.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


# Schedule kinds
KIND_POWER_CYCLE = "power_cycle"
KIND_MAINTENANCE = "maintenance"
KNOWN_KINDS = (KIND_POWER_CYCLE, KIND_MAINTENANCE)

# Recurrences
REC_ONCE = "once"
REC_DAILY = "daily"
REC_WEEKLY = "weekly"
KNOWN_RECURRENCES = (REC_ONCE, REC_DAILY, REC_WEEKLY)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "sch")
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    recurrence: Mapped[str] = mapped_column(String(20), nullable=False, default=REC_DAILY)

    # 'HH:MM' UTC (for daily/weekly) or empty for 'once'
    at_time_utc: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Array of weekday integers 0=Mon..6=Sun (only for 'weekly')
    weekdays: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # ISO datetime — only for 'once' recurrence
    start_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)

    # For maintenance schedules: how long to hold maintenance ON
    # before flipping OFF. Power-cycle ignores this.
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Target (only for power_cycle): same shape as watchdog rule target.
    # {"kind": "device" | "group" | "tag", "id": "...", "tag": "..."}
    target: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # power_cycle action params
    power_off_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    post_reboot_holdoff_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180
    )

    # Runtime state
    last_run_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    next_run_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()


Index(
    "ix_schedules_enabled_next_run",
    Schedule.enabled, Schedule.next_run_at,
)
