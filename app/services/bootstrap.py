from __future__ import annotations

import logging

from argon2 import PasswordHasher
from sqlalchemy import inspect, select

from app.config import Settings
from app.db import get_engine, session_scope
from app.models import Base, User

log = logging.getLogger(__name__)
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, candidate: str) -> bool:
    try:
        _hasher.verify(stored_hash, candidate)
        return True
    except Exception:
        return False


_SCHEMA_LOCK_KEY = 4242117309  # arbitrary stable bigint for pg_advisory_lock


def ensure_schema() -> bool:
    """Always run Base.metadata.create_all() under an advisory lock.

    create_all() is idempotent — it issues CREATE TABLE IF NOT EXISTS for each
    table and a no-op for indexes that already exist. v0.2.5: this used to
    short-circuit if `users` existed, which meant new tables added in a
    later release silently never got created on existing databases. Run
    every startup; it's cheap.

    create_all() does NOT issue ALTER TABLE for new columns on tables that
    already exist — _ensure_columns() handles those one-by-one with
    ADD COLUMN IF NOT EXISTS.

    The `pg_advisory_lock` serialises concurrent gunicorn workers racing
    this path on the production Postgres database. It is a Postgres-only
    function, so it is skipped on any other dialect — the SQLite
    unit-test backend is a single in-process connection with no worker
    race to guard against.
    """
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        use_advisory_lock = conn.dialect.name == "postgresql"
        if use_advisory_lock:
            conn.execute(
                text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY}
            )
        try:
            inspector = inspect(conn)
            fresh = not inspector.has_table("users")
            if fresh:
                log.info("Bootstrapping schema — running Base.metadata.create_all()")
            Base.metadata.create_all(bind=conn)
            _ensure_columns(conn)
            # org-boundary bootstrap fix (2026-05-21): add the
            # `organization_id` column to every PRE-EXISTING table that
            # gained it with the org work. `create_all()` above creates
            # the `organizations` table and any wholly-new org-era table,
            # but it does NOT add the new column to tables that predate
            # the org release — only this ALTER does. This MUST run
            # before `ensure_default_organization_backfill()`, whose
            # `UPDATE ... SET organization_id` would otherwise crash on
            # an upgraded production database.
            _ensure_org_id_columns(conn)
            return fresh
        finally:
            if use_advisory_lock:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": _SCHEMA_LOCK_KEY},
                )


# Idempotent ADD COLUMN steps for columns added after a table's first ship.
# Each entry is (table, column_name, column_ddl). Postgres only.
_PENDING_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("devices", "is_qa_fixture", "BOOLEAN NOT NULL DEFAULT FALSE"),  # v0.2.8
    ("devices", "is_protected",  "BOOLEAN NOT NULL DEFAULT FALSE"),  # v0.3.2
    ("devices", "is_held_off",   "BOOLEAN NOT NULL DEFAULT FALSE"),  # v0.3.2
    ("watchdog_rules", "failure_streak",  "INTEGER NOT NULL DEFAULT 0"),       # v0.4.2
    ("watchdog_rules", "recovery_streak", "INTEGER NOT NULL DEFAULT 0"),       # v0.4.2
    ("watchdog_rules", "last_probed_at",  "TIMESTAMPTZ"),                       # v0.4.2
    ("watchdog_rules", "last_action_at",  "TIMESTAMPTZ"),                       # v0.4.2
    ("watchdog_rules", "last_outcome",    "VARCHAR(40)"),                       # v0.4.2
    # v0.5.7 (B20): enrollment-token can target an existing device for
    # restore-after-reflash; null = current "create fresh row" behavior.
    ("enrollment_tokens", "target_device_id", "VARCHAR(40) REFERENCES devices(id) ON DELETE SET NULL"),
    # v0.5.22 (B21): desired-config blob + drift detection. All five
    # columns are additive nullable — NULL = no operator intent set,
    # behaviour stays identical to today.
    ("devices", "desired_config", "JSONB"),
    ("devices", "desired_mode", "VARCHAR(40)"),
    ("devices", "last_reported_config", "JSONB"),
    ("devices", "desired_config_updated_at", "TIMESTAMPTZ"),
    ("devices", "last_config_pushed_at", "TIMESTAMPTZ"),
    # v0.5.23 (B17 adjacent integrations): per-kind extras for HA /
    # weather / iCal integrations. Existing Roku sources keep NULL +
    # behave exactly as before; new kinds populate it.
    ("external_sensor_sources", "config", "JSONB"),
    # v0.5.38 (B1 RBAC P4): invitations can carry scope bindings that
    # will be granted on redemption. NULL = legacy global role only.
    ("invitations", "scope_payload", "JSONB"),
    # v0.5.51 (P0.1): absorb the firmware status/recovery/central contract
    # (firmware 0.1.19-dev-central-safe+). 16 history columns on
    # device_heartbeats (per-heartbeat snapshot) + 8 hot columns on devices
    # (current truth for fast filtering). All additive nullable — NULL = the
    # field was never reported. See docs/notes/2026-05-14-firmware-status-
    # and-recovery-contract.md.
    ("device_heartbeats", "recovery_mode", "BOOLEAN"),
    ("device_heartbeats", "auto_recovery_triggered", "BOOLEAN"),
    ("device_heartbeats", "last_known_good_restored", "BOOLEAN"),
    ("device_heartbeats", "consecutive_unhealthy_boots", "INTEGER"),
    ("device_heartbeats", "in_captive_portal", "BOOLEAN"),
    ("device_heartbeats", "holdoff_remaining_seconds", "INTEGER"),
    ("device_heartbeats", "cooldown_remaining_seconds", "INTEGER"),
    ("device_heartbeats", "central_enabled", "BOOLEAN"),
    ("device_heartbeats", "central_registered", "BOOLEAN"),
    ("device_heartbeats", "central_state", "VARCHAR(40)"),
    ("device_heartbeats", "central_device_id", "VARCHAR(40)"),
    ("device_heartbeats", "central_heartbeat_age_seconds", "INTEGER"),
    ("device_heartbeats", "power_analytics_enabled", "BOOLEAN"),
    ("device_heartbeats", "power_chip_type", "VARCHAR(40)"),
    ("device_heartbeats", "power_sample_rate_hz", "INTEGER"),
    ("device_heartbeats", "power_batch_seconds", "INTEGER"),
    ("devices", "reported_recovery_mode", "BOOLEAN"),
    ("devices", "reported_auto_recovery_triggered", "BOOLEAN"),
    ("devices", "reported_last_known_good_restored", "BOOLEAN"),
    ("devices", "reported_consecutive_unhealthy_boots", "INTEGER"),
    ("devices", "reported_in_captive_portal", "BOOLEAN"),
    ("devices", "reported_central_enabled", "BOOLEAN"),
    ("devices", "reported_central_registered", "BOOLEAN"),
    ("devices", "reported_central_state", "VARCHAR(40)"),
    # v0.5.55 (P1.2): per-rollup synthetic-sample count for data-quality
    # surfacing. Nullable — pre-P1.2 rollups stay NULL.
    ("device_power_rollups", "synthetic_sample_count", "INTEGER"),
    # v0.6.3 (devices-page correctness): real last-contact timestamp,
    # refreshed on every authenticated device request + on /announce
    # for an already-registered device. Additive nullable — NULL = not
    # seen since the column shipped; online/offline falls back to
    # last_heartbeat_at for those rows.
    ("devices", "last_seen_at", "TIMESTAMPTZ"),
    # v0.5.66 (P1.3): firmware 0.1.27+ low-load current semantics — when
    # measured current is clamped below ~50 mA, `i_ma_estimated` is True
    # and `i_ma_estimate` carries the firmware standby estimate.
    ("device_power_samples", "i_ma_estimated", "BOOLEAN"),
    ("device_power_samples", "i_ma_estimate", "INTEGER"),
    # Tier-2: heartbeat-folded power summary. The firmware retires the
    # dedicated /device/power-samples endpoint and folds a compact `power`
    # object into the heartbeat carrying the interval's min/avg/max watts.
    # A `source="heartbeat"` DevicePowerSample row stores avg in `p_w` and
    # the extremes here. Both additive nullable.
    ("device_power_samples", "min_w", "NUMERIC(8,2)"),
    ("device_power_samples", "max_w", "NUMERIC(8,2)"),
    # 0.6.8 (firmware 0.2.7+): current-connection WiFi RSSI (dBm) per
    # heartbeat. Additive nullable — NULL for pre-0.2.7 heartbeats.
    ("device_heartbeats", "wifi_rssi_dbm", "INTEGER"),
    # 0.6.10 (firmware 0.2.8+, #154): latest periodic nearby-network scan
    # snapshot on the device (JSON list of {ssid,rssi}) + its timestamp.
    ("devices", "last_wifi_scan", "JSONB"),
    ("devices", "last_wifi_scan_at", "TIMESTAMPTZ"),
)


def _ensure_columns(conn) -> None:
    """Idempotently add every `_PENDING_COLUMNS` entry.

    Production is Postgres, where `ALTER TABLE ADD COLUMN IF NOT EXISTS`
    is the proven, atomic idempotent form — kept exactly as before. The
    unit-test suite runs on SQLite, whose `ALTER TABLE` has no
    `IF NOT EXISTS` clause; there the column's presence is checked via
    the inspector first and a plain `ADD COLUMN` is issued for the
    missing ones. The Postgres path is byte-for-byte unchanged.
    """
    from sqlalchemy import text

    is_postgres = conn.dialect.name == "postgresql"
    inspector = None if is_postgres else inspect(conn)
    existing_tables = None if is_postgres else set(inspector.get_table_names())
    for table, column, ddl in _PENDING_COLUMNS:
        if is_postgres:
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                    f"{column} {ddl}"
                )
            )
            continue
        # Non-Postgres (SQLite test path): no IF NOT EXISTS — inspect.
        if table not in existing_tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if column in columns:
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


# org-boundary bootstrap fix (2026-05-21): the pre-existing tables that
# gained an `organization_id` column with the org-boundary work. These
# tables existed in the baseline schema (migration 0001) BEFORE the org
# release, so on an upgraded production database `create_all()` does NOT
# add the new column to them — only `ALTER TABLE ADD COLUMN` does. New
# org-era tables (organizations, organization_memberships, api_tokens,
# webhook_channels, notification_subscriptions, webhook_deliveries) are
# created whole by `create_all()` and need no entry here.
#
# This list is the union of:
#   * the 10 Tier-A tables of Alembic 0003 (`_TIER_A_TABLES`),
#   * `device_announcements` from Alembic 0004, and
#   * `audit_events_archive` (the un-FK'd mirror column, also 0003).
# It must stay in sync with `_ORG_TIER_A_TABLES` below (plus the archive
# table, which the data backfill does not touch but whose column the
# models still expect — see `AuditEventArchive.organization_id`).
#
# The column type is `VARCHAR(40)` — matching `String(40)` in the
# `TenantScoped` mixin / `tenant_scoped_org_column()` and `sa.String(40)`
# in Alembic 0003/0004. It is added NULLABLE here; the NOT-NULL flip is
# Alembic 0007's job and is intentionally not part of the bootstrap (the
# `_PENDING_*` lists are frozen as of the org release — design §6.2).
# No FK is added — `ADD COLUMN` cannot portably add an inline FK on
# SQLite, the live production DB is Postgres where the FK is carried by
# Alembic 0003/0004, and a missing FK never breaks the backfill or the
# `do_orm_execute` / `before_flush` org filter (which key off the column
# value, not the constraint).
_ORG_ID_PREEXISTING_TABLES: tuple[str, ...] = (
    "sites",
    "groups",
    "watchdog_rules",
    "schedules",
    "scenes",
    "enrollment_tokens",
    "external_sensor_sources",
    "role_bindings",
    "invitations",
    "audit_events",
    "device_announcements",
    "audit_events_archive",
)


def _ensure_org_id_columns(conn) -> None:
    """Add the `organization_id` column to every pre-existing table that
    gained it with the org-boundary work — org-boundary bootstrap fix.

    The org release added `organization_id` to the SQLAlchemy models, to
    `ensure_default_organization_backfill()` and to the Alembic
    migrations (0003/0004) — but the column-CREATION on existing tables
    was never wired into the startup bootstrap. The hub does not run
    Alembic at runtime; production schema is managed entirely by
    `ensure_schema()` (`create_all` + `_ensure_columns`). `create_all`
    only creates missing TABLES, never columns on existing ones, so on an
    upgraded production database `organization_id` was created on ZERO
    pre-existing tenant tables and `ensure_default_organization_backfill`
    crashed on `UPDATE ... SET organization_id`.

    This closes that gap. It mirrors `_ensure_columns()` — an idempotent
    `ADD COLUMN` per table — but is dialect-portable: SQLite's
    `ALTER TABLE` has no `ADD COLUMN IF NOT EXISTS`, so the column's
    presence is checked via the inspector first (a fresh `create_all()`
    database already has it from the model definition; a re-run of
    bootstrap on an already-upgraded database also has it).
    """
    from sqlalchemy import text

    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table in _ORG_ID_PREEXISTING_TABLES:
        if table not in existing_tables:
            # The table itself does not exist yet — `create_all()` has
            # just created it whole (with `organization_id` already in
            # it) or it is genuinely absent. Either way, nothing to ALTER.
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "organization_id" in columns:
            continue
        log.info(
            "org-boundary bootstrap: adding organization_id column to "
            "pre-existing table %s",
            table,
        )
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN organization_id VARCHAR(40)")
        )


# v0.5.36 (P2): Constraints that must run AFTER backfills. Each entry
# is (table, constraint_check_query, alter_ddl). The check_query should
# return True if the constraint is already applied.
_PENDING_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    # Device.site_id NOT NULL — only safe after ensure_device_site_id_backfill()
    (
        "devices",
        "SELECT is_nullable = 'NO' FROM information_schema.columns "
        "WHERE table_name = 'devices' AND column_name = 'site_id'",
        "ALTER TABLE devices ALTER COLUMN site_id SET NOT NULL",
    ),
)


def _ensure_constraints(conn) -> None:
    """Apply pending constraints that depend on backfills having run.
    Called from run_startup_bootstrap() AFTER the backfills.

    The `_PENDING_CONSTRAINTS` entries are Postgres-specific — both the
    `information_schema.columns` check query and the `ALTER COLUMN ...
    SET NOT NULL` statement are Postgres-only DDL. Production is
    Postgres, so this is correct there. On any other dialect (the SQLite
    unit-test path) this is a no-op: the test schema is built directly
    by `create_all()`, which already declares each column's final
    nullability from the model definition, so there is no post-backfill
    constraint to apply.
    """
    from sqlalchemy import text

    if conn.dialect.name != "postgresql":
        return

    for table, check_query, alter_ddl in _PENDING_CONSTRAINTS:
        already_applied = conn.execute(text(check_query)).scalar()
        if already_applied:
            continue
        log.info("Applying pending constraint on %s: %s", table, alter_ddl)
        conn.execute(text(alter_ddl))


# v0.5.0 (A1): one-shot RBAC role-binding backfill. Runs once per
# database; tracked via a `runtime_settings` row.
_RBAC_BACKFILL_KEY = "rbac.role_bindings_backfilled_at"


def ensure_role_bindings_backfill() -> None:
    """Populate `role_bindings` from the legacy `users` columns per
    B10 Q2 (RFC-003 §9.0):

    - existing super_admin → ('global', NULL, 'super_admin')
    - existing admin (not super) → one row per current site_id;
      if no sites exist yet, one ('global', NULL, 'admin') as a
      safety net so we don't lock the operator out on day 1.
    - existing operator → no rows (forced re-grant by an admin
      before the enforce flip).

    Idempotent: if `runtime_settings[rbac.role_bindings_backfilled_at]`
    is set, do nothing. The shadow-mode middleware (A2) will start
    *logging* would-have-denied requests against these bindings
    without enforcing; the enforce flip is a separate ship (A8)
    gated on ≥ 7 days of clean shadow logs.
    """
    from datetime import datetime, timezone
    from sqlalchemy import delete, text
    from app.models import RoleBinding, Site
    from app.models.users import (
        ROLE_ADMIN,
        ROLE_OPERATOR,
        ROLE_SUPER_ADMIN,
        ROLE_VIEWER,
    )
    from app.models.role_bindings import (
        SCOPE_GLOBAL,
        SCOPE_SITE,
    )
    from app.services import runtime_settings as rs

    # v0.5.1: corrective step. v0.5.0's backfill (shipped 2026-05-10
    # 23:53 UTC) gated on `users.is_admin` which is also True for
    # users with role='operator' in this schema — so every active
    # operator got incorrect site-admin bindings they should not have
    # per B10 Q2. Delete every binding that doesn't match the user's
    # actual role + clear the tracking row so the corrected backfill
    # below re-runs idempotently. Tracked separately so this never
    # re-runs after v0.5.1.
    _RBAC_V050_CORRECTION_KEY = "rbac.role_bindings_v050_correction_applied_at"
    if not rs.has_db_value(_RBAC_V050_CORRECTION_KEY):
        with session_scope() as session:
            # Drop bindings created by the v0.5.0 bug: every binding
            # for a user whose actual `role` is operator / viewer.
            bad_user_ids = list(session.scalars(
                select(User.id).where(
                    User.role.in_((ROLE_OPERATOR, ROLE_VIEWER))
                )
            ))
            if bad_user_ids:
                deleted = session.execute(
                    delete(RoleBinding).where(
                        RoleBinding.user_id.in_(bad_user_ids)
                    )
                ).rowcount
                log.warning(
                    "v0.5.1 RBAC correction: deleted %d role_bindings "
                    "rows that v0.5.0's backfill incorrectly created for "
                    "operator/viewer users",
                    deleted,
                )
            # Also clear the original tracking key so the corrected
            # backfill below re-runs. If admin rows somehow ALSO got
            # double-inserted (e.g., gunicorn-worker race in v0.5.0),
            # we also need to dedupe them. UniqueConstraint on
            # (user_id, scope_type, scope_id) covers post-correction.
            session.execute(
                text(
                    "DELETE FROM role_bindings rb1 "
                    "USING role_bindings rb2 "
                    "WHERE rb1.id > rb2.id "
                    "AND rb1.user_id = rb2.user_id "
                    "AND rb1.scope_type = rb2.scope_type "
                    "AND rb1.scope_id IS NOT DISTINCT FROM rb2.scope_id"
                )
            )
        rs.set_(_RBAC_V050_CORRECTION_KEY, datetime.now(timezone.utc).isoformat())
        # Don't reset _RBAC_BACKFILL_KEY — we don't want to fully
        # re-run on each container start. Correction is one-shot.

    if rs.has_db_value(_RBAC_BACKFILL_KEY):
        return  # already done

    log.info("Running one-shot RBAC role-binding backfill (v0.5.1 / A1)")
    now = datetime.now(timezone.utc)
    inserted = 0
    with session_scope() as session:
        users = list(session.scalars(select(User).where(User.is_active.is_(True))))
        sites = list(session.scalars(select(Site)))
        site_ids = [s.id for s in sites]

        # org-boundary phase 3: `role_bindings.organization_id` is NOT
        # NULL. This backfill runs inside `tenant_scope.system()` (the
        # before_flush stamping is a no-op there), so the org must be
        # set explicitly. The default-organization backfill runs first
        # (see `_run_startup_bootstrap_inner`), so it is resolvable.
        org_id = resolve_default_org_id(session)
        if org_id is None:
            log.warning(
                "RBAC role-bindings backfill: no organization resolvable "
                "— role_bindings rows cannot be created. Skipping; the "
                "default-organization backfill should have run first."
            )
            return

        for u in users:
            if u.is_super_admin:
                session.add(RoleBinding(
                    organization_id=org_id,
                    user_id=u.id,
                    scope_type=SCOPE_GLOBAL,
                    scope_id=None,
                    role=ROLE_SUPER_ADMIN,
                    created_at=now,
                    updated_at=now,
                    created_by_user_id=None,
                ))
                inserted += 1
                continue
            # v0.5.1 fix: gate on the actual `role` column, NOT the
            # legacy `is_admin` boolean (which is True for operators
            # in this schema and led to over-granting in v0.5.0).
            if u.role == ROLE_ADMIN:
                if site_ids:
                    for sid in site_ids:
                        session.add(RoleBinding(
                            organization_id=org_id,
                            user_id=u.id,
                            scope_type=SCOPE_SITE,
                            scope_id=sid,
                            role=ROLE_ADMIN,
                            created_at=now,
                            updated_at=now,
                            created_by_user_id=None,
                        ))
                        inserted += 1
                else:
                    # Safety net: no sites yet, give global admin so the
                    # operator can still configure things. Will be tightened
                    # when sites get created and the operator manually
                    # re-scopes.
                    session.add(RoleBinding(
                        organization_id=org_id,
                        user_id=u.id,
                        scope_type=SCOPE_GLOBAL,
                        scope_id=None,
                        role=ROLE_ADMIN,
                        created_at=now,
                        updated_at=now,
                        created_by_user_id=None,
                    ))
                    inserted += 1
            # operator / viewer → no rows (forced re-grant per B10 Q2)

    # Mark backfill complete so this never runs again
    rs.set_(_RBAC_BACKFILL_KEY, now.isoformat())
    log.info("RBAC backfill inserted %d role_bindings rows", inserted)


# v0.5.36 (B1 RBAC P2): one-shot Device.site_id NOT NULL backfill.
# Runs once per database; tracked via a `runtime_settings` row.
_DEVICE_SITE_ID_BACKFILL_KEY = "device.site_id_not_null_backfilled_at"


def ensure_device_site_id_backfill() -> None:
    """Backfill Device.site_id for any devices with site_id=NULL before
    enforcing NOT NULL constraint via _PENDING_COLUMNS.

    Strategy (per design doc §8 Q1):
    - If exactly one site exists, assign all NULL devices to that site.
    - Otherwise, create a site named "Default" and use it.

    Idempotent: if `runtime_settings[device.site_id_not_null_backfilled_at]`
    is set, do nothing.
    """
    from datetime import datetime, timezone
    from sqlalchemy import text
    from app.models import Device, Site
    from app.services import runtime_settings as rs

    if rs.has_db_value(_DEVICE_SITE_ID_BACKFILL_KEY):
        return  # already done

    log.info("Running one-shot Device.site_id NOT NULL backfill (v0.5.36 / P2)")
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        # Count devices with null site_id
        from sqlalchemy import func
        null_count = session.scalar(
            select(func.count()).select_from(Device).where(Device.site_id.is_(None))
        ) or 0

        if null_count == 0:
            log.info("No devices with null site_id — backfill complete")
            rs.set_(_DEVICE_SITE_ID_BACKFILL_KEY, now.isoformat())
            return

        # Determine target site
        sites = list(session.scalars(select(Site)))
        if len(sites) == 1:
            # Reuse the single existing site
            target_site_id = sites[0].id
            log.info(
                "Backfilling %d device(s) with null site_id to single existing site %s",
                null_count,
                target_site_id,
            )
        else:
            # Create "Default" site
            from functools import partial
            from app.models._helpers import new_id

            # org-boundary phase 3: `sites.organization_id` is NOT NULL.
            # This backfill runs inside `tenant_scope.system()` (no
            # before_flush stamping), so the org is set explicitly. The
            # default-organization backfill runs first, so it resolves.
            site_org_id = resolve_default_org_id(session)
            if site_org_id is None:
                log.warning(
                    "Device.site_id backfill: no organization resolvable "
                    "— cannot create the Default site. Skipping; the "
                    "default-organization backfill should have run first."
                )
                rs.set_(_DEVICE_SITE_ID_BACKFILL_KEY, now.isoformat())
                return

            target_site_id = partial(new_id, "site")()
            default_site = Site(
                id=target_site_id,
                name="Default",
                organization_id=site_org_id,
                created_at=now,
                updated_at=now,
            )
            session.add(default_site)
            session.flush()
            log.info(
                "Created Default site %s; backfilling %d device(s) with null site_id",
                target_site_id,
                null_count,
            )

        # Update all NULL site_id devices
        updated = session.execute(
            text(
                "UPDATE devices SET site_id = :target_site_id "
                "WHERE site_id IS NULL"
            ),
            {"target_site_id": target_site_id}
        ).rowcount

        log.info("Backfilled %d devices with site_id=%s", updated, target_site_id)

    # Mark backfill complete
    rs.set_(_DEVICE_SITE_ID_BACKFILL_KEY, now.isoformat())


# org-boundary phase 1: one-shot default-organization backfill. Runs
# once per database; tracked via a `runtime_settings` row. Modeled on
# ensure_device_site_id_backfill above. See
# docs/notes/2026-05-20-organization-boundary-design.md section 6.1.
_ORG_BACKFILL_KEY = "organization.default_backfilled_at"

# The Tier-A tables that carry a nullable organization_id column (the
# TenantScoped models). Every NULL organization_id row is assigned to the
# one default org by the backfill below.
#
# org-boundary phase 2: `device_announcements` was added — phase 1's list
# omitted it, but the design doc §2 lists it as a Tier-A entity. Its
# column is added by migration 0004. An un-adopted announcement may
# legitimately have no org, so the column stays nullable permanently;
# the backfill still stamps the pre-existing rows so a default-org
# install has them attributed.
_ORG_TIER_A_TABLES: tuple[str, ...] = (
    "sites",
    "groups",
    "watchdog_rules",
    "schedules",
    "scenes",
    "enrollment_tokens",
    "external_sensor_sources",
    "role_bindings",
    "invitations",
    "audit_events",
    "device_announcements",
)


def resolve_default_org_id(session) -> str | None:
    """Return an `organization_id` for a Tier-A row created during
    startup bootstrap — org-boundary phase 3.

    The other startup backfills (`ensure_role_bindings_backfill`,
    `ensure_device_site_id_backfill`) create Tier-A rows
    (`role_bindings`, the "Default" `Site`) inside `tenant_scope.
    system()`, where the `before_flush` org-stamping is intentionally a
    no-op. Phase 3 made `organization_id` NOT NULL on those tables, so
    those rows must carry an org explicitly. This resolves it:

      1. the org named/slugged "default" (the backfill's own org), else
      2. the only org, if exactly one exists, else
      3. None — the caller leaves the column unset (only possible on a
         DB that genuinely has no org yet; the org backfill, which now
         runs first, makes that case unreachable in normal startup).

    Operates within the caller's session so the lookup sees rows the
    same transaction created.
    """
    from app.models import Organization

    org = session.scalar(
        select(Organization).where(Organization.slug == "default")
    )
    if org is not None:
        return org.id
    orgs = list(session.scalars(select(Organization)))
    return orgs[0].id if len(orgs) == 1 else None


def ensure_default_organization_backfill() -> None:
    """Assign every existing row to one default organization.

    Phase 1 of the multi-tenant organization boundary (design section
    6.1). Idempotent and one-shot, tracked via a `runtime_settings` row
    under `organization.default_backfilled_at` — modeled exactly on
    `ensure_device_site_id_backfill` above.

    Steps:
      1. If any `organizations` row already exists -> done.
      2. Create one default `Organization` (name "Default Organization",
         slug "default", status active, plan "legacy"). Its
         `owner_user_id` is the first super-admin user, or NULL if the
         install has none yet.
      3. UPDATE every Tier-A table — set `organization_id` to the
         default org id where it is NULL.
      4. Create one `OrganizationMembership` per existing user — owner
         for the org owner, admin/member mapped from `users.role`.
      5. Mark `organization.default_backfilled_at`.

    org-boundary phase 3: this backfill now runs FIRST in the startup
    sequence — before `ensure_role_bindings_backfill()` and
    `ensure_device_site_id_backfill()`. Those two create Tier-A rows
    (`role_bindings`, the "Default" `Site`) whose `organization_id`
    became NOT NULL in phase 3, so the default org must already exist
    for them to be stamped (via `resolve_default_org_id`). Step 3's
    `UPDATE ... WHERE organization_id IS NULL` still stamps any
    pre-existing rows on an upgraded database; on a fresh `create_all()`
    database the Tier-A tables are empty at this point, so step 3 is a
    no-op and the rows created by the later backfills are born stamped.

    The constraint-hardening migration (alembic 0005) flips those
    columns to NOT NULL; it runs after this backfill is confirmed on
    every database (design §8.1 steps 3–4).
    """
    from datetime import datetime, timezone
    from functools import partial
    from sqlalchemy import func, text
    from app.models import Organization, OrganizationMembership, User
    from app.models._helpers import new_id
    from app.models.organizations import (
        ORG_PLAN_LEGACY,
        ORG_ROLE_ADMIN,
        ORG_ROLE_MEMBER,
        ORG_ROLE_OWNER,
        ORG_STATUS_ACTIVE,
    )
    from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
    from app.services import runtime_settings as rs

    if rs.has_db_value(_ORG_BACKFILL_KEY):
        return  # already done

    now = datetime.now(timezone.utc)

    with session_scope() as session:
        # Step 1: idempotency — if any organization already exists,
        # treat the backfill as complete and just mark it.
        existing_org_count = session.scalar(
            select(func.count()).select_from(Organization)
        ) or 0
        if existing_org_count > 0:
            log.info(
                "organizations table is non-empty (%d row(s)); "
                "skipping default-organization backfill",
                existing_org_count,
            )
            rs.set_(_ORG_BACKFILL_KEY, now.isoformat())
            return

        log.info(
            "Running one-shot default-organization backfill "
            "(org-boundary phase 1)"
        )

        # Step 2: pick the owner — the first super-admin user, mirroring
        # ensure_bootstrap_admin's lookup style. May be None on an
        # install with no super-admin yet; that is acceptable (the FK is
        # nullable / SET NULL) but worth a loud log.
        owner = session.scalar(
            select(User)
            .where(User.is_super_admin.is_(True))
            .order_by(User.created_at)
        )
        if owner is None:
            log.warning(
                "default-organization backfill: no super-admin user "
                "found — the default organization will be created "
                "ownerless (owner_user_id=NULL). A platform admin "
                "should assign ownership."
            )

        org_id = partial(new_id, "org")()
        default_org = Organization(
            id=org_id,
            name="Default Organization",
            slug="default",
            status=ORG_STATUS_ACTIVE,
            plan=ORG_PLAN_LEGACY,
            # Single-tenant marker is left False here — a self-host
            # install gets is_self_hosted_default set by a dedicated
            # bootstrap step in phase 2 (design section 5.3). Phase 1
            # keeps the backfill conservative.
            is_self_hosted_default=False,
            owner_user_id=owner.id if owner is not None else None,
            created_at=now,
            updated_at=now,
        )
        session.add(default_org)
        session.flush()

        # Step 3: stamp every Tier-A row that has no org yet. Raw UPDATE
        # per table, exactly like ensure_device_site_id_backfill's
        # `UPDATE devices ...`. organization_id is a real column on each
        # of these tables after migration 0003 (or after create_all on a
        # fresh DB).
        total_updated = 0
        for table in _ORG_TIER_A_TABLES:
            updated = session.execute(
                text(
                    f"UPDATE {table} SET organization_id = :org_id "
                    f"WHERE organization_id IS NULL"
                ),
                {"org_id": org_id},
            ).rowcount
            total_updated += updated
            if updated:
                log.info(
                    "default-organization backfill: assigned %d %s "
                    "row(s) to org %s",
                    updated,
                    table,
                    org_id,
                )
        log.info(
            "default-organization backfill: %d Tier-A row(s) assigned "
            "to org %s across %d tables",
            total_updated,
            org_id,
            len(_ORG_TIER_A_TABLES),
        )

        # Step 4: one OrganizationMembership per existing user. The org
        # owner gets org_role 'owner'; everyone else maps from their
        # legacy users.role — admin/super_admin -> 'admin', otherwise
        # 'member'.
        users = list(session.scalars(select(User)))
        membership_count = 0
        for u in users:
            if owner is not None and u.id == owner.id:
                org_role = ORG_ROLE_OWNER
            elif u.role in (ROLE_ADMIN, ROLE_SUPER_ADMIN) or u.is_super_admin:
                org_role = ORG_ROLE_ADMIN
            else:
                org_role = ORG_ROLE_MEMBER
            session.add(
                OrganizationMembership(
                    id=partial(new_id, "om")(),
                    organization_id=org_id,
                    user_id=u.id,
                    org_role=org_role,
                    created_at=now,
                )
            )
            membership_count += 1
        log.info(
            "default-organization backfill: created %d "
            "organization_membership row(s)",
            membership_count,
        )

    # Step 5: mark complete so this never runs again.
    rs.set_(_ORG_BACKFILL_KEY, now.isoformat())
    log.info("default-organization backfill complete")
    # org-boundary phase 3: the NOT-NULL flip, per-org unique
    # constraints and FK on-delete swaps now ship as Alembic revision
    # 0007_org_constraint_hardening (design §6.2, §6.3). That migration
    # runs once this backfill is confirmed on every database — it is
    # NOT slotted into _ensure_constraints (the `_PENDING_*` lists are
    # frozen as of the org release; new schema changes go through
    # Alembic, design §6.2).


def ensure_bootstrap_admin(settings: Settings) -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    from app.models.users import ROLE_SUPER_ADMIN

    with session_scope() as session:
        existing = session.scalar(
            select(User).where(User.email == settings.bootstrap_admin_email)
        )
        if existing is not None:
            # v0.4.16 (BUG-046): only reconcile privileges by default.
            # Pre-fix this also force-overwrote the password on EVERY
            # container startup, silently nuking any password the
            # operator had legitimately reset via /app/reset-password.
            # The "I forgot my password — recover via env var" flow
            # is preserved behind a deliberate opt-in env var:
            # REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP=1.
            if settings.bootstrap_admin_force_password_on_startup:
                existing.password_hash = hash_password(settings.bootstrap_admin_password)
                log.info(
                    "Bootstrap admin password force-reconciled (env-gated)"
                )
            existing.is_admin = True
            existing.is_active = True
            existing.is_super_admin = True
            # v0.5.80: the `role` string column (added with the v0.5.0
            # RBAC migration) was never set here, so the bootstrap admin
            # kept the User.role default of "admin" despite is_super_admin
            # being True — role-checked endpoints (maintenance toggle,
            # user management) then 403'd it. Reconcile it to match.
            existing.role = ROLE_SUPER_ADMIN
            session.add(existing)
            return
        log.info(
            "Creating bootstrap admin %s from REBOOTER_BOOTSTRAP_ADMIN_* env vars",
            settings.bootstrap_admin_email,
        )
        admin = User(
            email=settings.bootstrap_admin_email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            display_name=settings.bootstrap_admin_email.split("@", 1)[0],
            is_admin=True,
            is_active=True,
            is_super_admin=True,
            # v0.5.80: without this the `role` column defaults to "admin"
            # (User.role default) — inconsistent with is_super_admin and
            # rejected by super-admin-only role checks.
            role=ROLE_SUPER_ADMIN,
        )
        session.add(admin)


def run_startup_bootstrap(settings: Settings) -> None:
    # org-boundary phase 2 (design §3.4): bootstrap runs at startup
    # before any request and legitimately touches every org's rows (it
    # creates the default org and stamps existing rows). It runs inside
    # an explicit `tenant_scope.system()` bypass so the do_orm_execute
    # read filter and the before_flush write-stamping are no-ops here —
    # never a bare unset ContextVar.
    from app.services import tenant_scope

    with tenant_scope.system():
        _run_startup_bootstrap_inner(settings)


def _run_startup_bootstrap_inner(settings: Settings) -> None:
    ensure_schema()
    ensure_bootstrap_admin(settings)
    # org-boundary phase 3: the default-organization backfill now runs
    # FIRST — before the RBAC and Device.site_id backfills. Those two
    # create Tier-A rows (`role_bindings`, the "Default" `Site`) whose
    # `organization_id` became NOT NULL in phase 3 (migration 0005); the
    # default org must therefore already exist so they can be stamped
    # with it (see `resolve_default_org_id`). The backfill is idempotent
    # and runtime_settings-tracked, so reordering it is safe on an
    # already-bootstrapped DB. On a fresh `create_all()` DB this also
    # means the new tables are never written ownerless.
    try:
        ensure_default_organization_backfill()
    except Exception:
        log.exception(
            "Default-organization backfill failed; container will continue"
        )
    # v0.5.0 (A1): one-shot RBAC backfill. Runs once per database;
    # idempotent (tracked via runtime_settings row).
    try:
        ensure_role_bindings_backfill()
    except Exception:
        # Never let the backfill block startup. If it errors we want
        # the container to come up anyway; the operator will see the
        # exception in logs and can re-run by deleting the tracking
        # runtime_setting row.
        log.exception("RBAC role-bindings backfill failed; container will continue")
    # v0.5.36 (P2): Device.site_id NOT NULL backfill before enforcing
    # the constraint via _PENDING_CONSTRAINTS.
    try:
        ensure_device_site_id_backfill()
    except Exception:
        log.exception("Device.site_id backfill failed; container will continue")
    # Apply constraints that depend on backfills (v0.5.36+)
    try:
        engine = get_engine()
        with engine.begin() as conn:
            _ensure_constraints(conn)
    except Exception:
        log.exception("Pending constraints application failed; container will continue")
