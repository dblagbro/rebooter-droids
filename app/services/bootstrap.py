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
    """
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        try:
            inspector = inspect(conn)
            fresh = not inspector.has_table("users")
            if fresh:
                log.info("Bootstrapping schema — running Base.metadata.create_all()")
            Base.metadata.create_all(bind=conn)
            _ensure_columns(conn)
            return fresh
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK_KEY})


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
)


def _ensure_columns(conn) -> None:
    from sqlalchemy import text

    for table, column, ddl in _PENDING_COLUMNS:
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        )


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

        for u in users:
            if u.is_super_admin:
                session.add(RoleBinding(
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


def ensure_bootstrap_admin(settings: Settings) -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
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
        )
        session.add(admin)


def run_startup_bootstrap(settings: Settings) -> None:
    ensure_schema()
    ensure_bootstrap_admin(settings)
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
