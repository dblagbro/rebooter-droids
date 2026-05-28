"""device_heartbeats.wifi_rssi_dbm

Revision ID: 0008_heartbeat_wifi_rssi
Revises: 0007_org_constraint_hardening
Create Date: 2026-05-28

Adds `wifi_rssi_dbm` (nullable int) to `device_heartbeats`. Firmware
0.2.7+ reports the current-connection RSSI in the heartbeat; the hub
stores it per-row so the device-detail page can chart WiFi signal
quality and flag degradation. On a fresh deployment
`Base.metadata.create_all()` already includes the column; this revision
keeps a migrated deployment in parity. NULL for pre-0.2.7 heartbeats or
when the device wasn't associated.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_heartbeat_wifi_rssi"
down_revision: Union[str, None] = "0007_org_constraint_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent ADD: a nullable int column can be pre-added to the live
    # table (safe while the old code runs, since it never references the
    # column) to eliminate the deploy-window where new code could hit a
    # missing column. IF NOT EXISTS makes this revision a no-op when the
    # column was already added that way. Postgres-only path; on SQLite a
    # fresh DB gets the column from Base.metadata.create_all().
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE device_heartbeats "
            "ADD COLUMN IF NOT EXISTS wifi_rssi_dbm INTEGER"
        )
    else:
        with op.batch_alter_table("device_heartbeats") as batch:
            batch.add_column(sa.Column("wifi_rssi_dbm", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE device_heartbeats DROP COLUMN IF EXISTS wifi_rssi_dbm")
    else:
        with op.batch_alter_table("device_heartbeats") as batch:
            batch.drop_column("wifi_rssi_dbm")
