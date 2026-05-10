"""Status-inbox attention acknowledgements — v0.4.22 (Tier-2 E).

When the operator clicks Ack/Snooze on a Status-page attention
item (e.g. `device_offline_long:dev_xxx`), a row gets stored here
and the inbox service filters that item out for the duration.

Snooze with `until` = NULL → permanent ack until the operator
manually clears (or the item's underlying state changes — e.g. a
device coming back online clears the offline_long item).

Snooze with `until` set → re-surface after that time.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class AttentionAck(Base):
    __tablename__ = "attention_acks"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "ack")
    )

    # The stable inbox item id, e.g. `device_offline_long:dev_xxx`,
    # `watchdog_firing:wdr_xxx`, `device_failsafe:dev_xxx:reason`.
    # Inbox items carry these as `item.id` already.
    attention_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True
    )

    acked_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acked_at: Mapped[datetime] = ts_column()

    # Snooze until — NULL means "ack forever". When set, the inbox
    # service treats the ack as expired once `now > snooze_until`.
    snooze_until: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


Index("ix_attention_acks_attention_id", AttentionAck.attention_id)
Index("ix_attention_acks_snooze_until", AttentionAck.snooze_until)
