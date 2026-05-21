"""Regression test — deploying the org-boundary release onto a
PRE-EXISTING, PRE-ORG database.

This is the deploy-critical regression test for the v0.6.0 production
incident: the org-boundary work added `organization_id` to the
SQLAlchemy models, to `ensure_default_organization_backfill()` and to
the Alembic migrations — but the column-CREATION on existing tables was
never wired into the startup bootstrap (`app/services/bootstrap.py`).

The hub does NOT run Alembic at runtime; production schema is managed
entirely by `run_startup_bootstrap()` -> `ensure_schema()`
(`create_all` + `_ensure_columns` + `_ensure_org_id_columns`) +
backfills + `_ensure_constraints()`. `create_all()` only creates missing
TABLES — it never adds a column to a table that already exists. So on
the production database, whose tenant tables predate the org work,
`organization_id` was created on ZERO tenant tables;
`ensure_default_organization_backfill()` then crashed on
`UPDATE sites SET organization_id=... WHERE organization_id IS NULL`,
and org-scoped code 500'd hub-wide.

This test reproduces that exact deploy:

  1. Build a database with the OLD (pre-org) schema — every tenant
     table WITHOUT `organization_id`, no `organizations` /
     `organization_memberships` tables — and seed a few tenant rows.
  2. Run the full `run_startup_bootstrap()` against it.
  3. Assert the org schema + data is now correct: the `organizations`
     table exists, EVERY tenant table has `organization_id`, a default
     organization row was created, every pre-existing tenant row has
     `organization_id` backfilled, and an org-scoped query succeeds.

It MUST fail against current `main` (the column-creation gap) and PASS
with the bootstrap fix.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import flask
import sqlalchemy as sa

from app.config import load_settings
from app.db import get_engine, init_engine, session_scope
from app.models import Base


# The tenant (TenantScoped) tables that PRE-EXIST the org work — they
# were created by the baseline schema (migration 0001) and only later
# gained `organization_id`. These are exactly the tables whose column
# `create_all()` cannot add on an upgraded DB. `audit_events_archive`
# is not TenantScoped but mirrors `audit_events.organization_id`, so the
# bootstrap must add its column too.
_PRE_ORG_TENANT_TABLES = (
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

# The org-era tables that did NOT exist before the org release.
_ORG_ERA_TABLES = ("organizations", "organization_memberships")


def _build_pre_org_database(tmp_path):
    """Create a throwaway SQLite database carrying the OLD, pre-org
    schema: every org-era table dropped and every `organization_id`
    column stripped. Returns `(database_url, pre_meta)`.

    The model classes are untouched — only this throwaway schema differs.
    This mirrors the real production state at deploy time: tenant tables
    that predate the org work and have no `organization_id` column.

    Built by reflecting the current `Base.metadata` into a fresh
    `MetaData`, dropping the org-era tables and rebuilding every
    pre-existing tenant table without its `organization_id` column (and
    without any constraint that references it — the per-org UNIQUE
    constraints on sites/groups/scenes and `uq_role_binding_scope`).
    `create_all` from that pre-org `MetaData` then yields a genuine
    old-schema database.
    """
    db_url = f"sqlite:///{tmp_path / 'rebooter-pre-org.sqlite'}"
    settings = replace(load_settings(), database_url=db_url)
    init_engine(settings)
    engine = get_engine()

    # Clone Base.metadata, then surgically remove every trace of the
    # org work so `create_all` builds a genuine pre-org schema.
    pre_meta = sa.MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(pre_meta)

    # Drop the org-era tables entirely.
    for name in _ORG_ERA_TABLES:
        t = pre_meta.tables.get(name)
        if t is not None:
            pre_meta.remove(t)

    # Rebuild every table that carries `organization_id` without that
    # column and without any constraint referencing it. Snapshot the
    # table list and each table's constraints/columns BEFORE mutating
    # `pre_meta` — never iterate a collection being modified.
    org_bearing = [
        name
        for name in list(pre_meta.tables.keys())
        if "organization_id" in pre_meta.tables[name].c
    ]
    for name in org_bearing:
        table = pre_meta.tables[name]
        kept_cols = [
            c._copy() for c in list(table.columns) if c.name != "organization_id"
        ]
        # Keep every constraint EXCEPT (a) the PrimaryKeyConstraint —
        # the copied columns already carry `primary_key=True`, so the PK
        # is re-derived automatically and copying it would dangle a
        # reference to the old columns — and (b) any constraint that
        # references `organization_id` (the org FK + the per-org UNIQUE
        # constraints on sites/groups/scenes and `uq_role_binding_scope`).
        kept_constraints = [
            con._copy()
            for con in list(table.constraints)
            if not isinstance(con, sa.PrimaryKeyConstraint)
            and "organization_id"
            not in {c.name for c in getattr(con, "columns", [])}
        ]
        pre_meta.remove(table)
        sa.Table(name, pre_meta, *kept_cols, *kept_constraints)

    pre_meta.drop_all(engine)
    pre_meta.create_all(engine)
    return db_url, pre_meta


def _org_column_present(engine, table: str) -> bool:
    return "organization_id" in {
        c["name"] for c in sa.inspect(engine).get_columns(table)
    }


def test_bootstrap_upgrades_a_pre_org_database(tmp_path):
    """Full `run_startup_bootstrap()` against a pre-org database creates
    the org schema, creates the default org, and backfills every
    pre-existing tenant row's `organization_id`.

    Fails on current `main` — the bootstrap never adds `organization_id`
    to the pre-existing tenant tables, so the default-org backfill's
    `UPDATE ... SET organization_id` raises and the org schema is never
    completed. Passes with the bootstrap fix.
    """
    from app.services.bootstrap import run_startup_bootstrap
    from app.services import tenant_scope

    db_url, pre_meta = _build_pre_org_database(tmp_path)
    engine = get_engine()

    # ── Sanity: the database really is in the old, pre-org shape. ──────
    inspector = sa.inspect(engine)
    table_names = set(inspector.get_table_names())
    for org_table in _ORG_ERA_TABLES:
        assert org_table not in table_names, (
            f"pre-org DB unexpectedly has {org_table!r}"
        )
    for tenant_table in _PRE_ORG_TENANT_TABLES:
        assert tenant_table in table_names, tenant_table
        assert not _org_column_present(engine, tenant_table), (
            f"pre-org DB unexpectedly has organization_id on {tenant_table!r}"
        )

    # ── Seed a handful of pre-existing tenant rows (no org anywhere). ──
    # The ORM models all declare `organization_id`, so an ORM INSERT
    # would emit a column the pre-org tables do not have. Tenant rows are
    # therefore seeded via Core `insert()` against the `pre_meta` tables
    # (which carry every Python column default but NOT `organization_id`)
    # — faithful to the real production state: rows that predate the org
    # work. `users` is NOT a tenant table (its schema is unchanged
    # pre/post-org) so the ORM `User` model is fine for it.
    from app.models import User
    from app.models._helpers import new_id, utcnow

    now = utcnow()
    admin_id = new_id("usr")
    operator_id = new_id("usr")
    site_a_id = new_id("site")
    site_b_id = new_id("site")

    def _pre(table: str):
        return pre_meta.tables[table]

    with flask.Flask(__name__).app_context():
        # Seed `users` via the ORM — `users` carries no organization_id.
        with tenant_scope.system():
            with session_scope() as s:
                s.add(
                    User(
                        id=admin_id,
                        email="admin@example.com",
                        password_hash="x",
                        role="super_admin",
                        is_super_admin=True,
                    )
                )
                s.add(
                    User(
                        id=operator_id,
                        email="op@example.com",
                        password_hash="x",
                        role="operator",
                        is_super_admin=False,
                    )
                )

        # Seed the tenant tables via Core insert on the pre-org metadata
        # tables — applies the Python column defaults, omits org_id.
        with engine.begin() as conn:
            conn.execute(
                _pre("sites").insert(),
                [
                    {"id": site_a_id, "name": "HQ", "created_at": now,
                     "updated_at": now},
                    {"id": site_b_id, "name": "Branch", "created_at": now,
                     "updated_at": now},
                ],
            )
            conn.execute(
                _pre("groups").insert(),
                {"id": new_id("grp"), "name": "Rack 1", "site_id": site_a_id,
                 "created_at": now, "updated_at": now},
            )
            conn.execute(
                _pre("watchdog_rules").insert(),
                {"id": new_id("wr"), "name": "ping gateway",
                 "created_at": now, "updated_at": now},
            )
            conn.execute(
                _pre("schedules").insert(),
                {"id": new_id("sch"), "name": "Nightly reboot",
                 "kind": "power_cycle", "created_at": now, "updated_at": now},
            )
            conn.execute(
                _pre("scenes").insert(),
                {"id": new_id("scn"), "name": "Movie Night",
                 "created_at": now, "updated_at": now},
            )
            conn.execute(
                _pre("invitations").insert(),
                {"id": new_id("inv"), "email": "invitee@example.com",
                 "role": "operator", "token_hash": "hash-123",
                 "expires_at": now + timedelta(days=7), "created_at": now},
            )
            conn.execute(
                _pre("role_bindings").insert(),
                {"id": new_id("rb"), "user_id": admin_id,
                 "scope_type": "global", "role": "admin",
                 "created_at": now, "updated_at": now},
            )

        # ── The deploy: run the FULL startup bootstrap. ───────────────
        settings = replace(load_settings(), database_url=db_url)
        run_startup_bootstrap(settings)

        # ── Assert 1: the `organizations` table now exists. ───────────
        engine = get_engine()
        post_tables = set(sa.inspect(engine).get_table_names())
        assert "organizations" in post_tables
        assert "organization_memberships" in post_tables

        # ── Assert 2: EVERY tenant table now has `organization_id`. ───
        for tenant_table in _PRE_ORG_TENANT_TABLES:
            assert _org_column_present(engine, tenant_table), (
                f"bootstrap did not add organization_id to {tenant_table!r}"
            )

        # ── Assert 3: a default organization row was created. ─────────
        from app.models import (
            Group,
            Invitation,
            Organization,
            OrganizationMembership,
            RoleBinding,
            Scene,
            Schedule,
            Site,
            WatchdogRule,
        )

        with tenant_scope.system():
            with session_scope() as s:
                orgs = list(s.scalars(sa.select(Organization)))
                assert len(orgs) == 1, "expected exactly one default org"
                default_org = orgs[0]
                assert default_org.slug == "default"
                assert default_org.name == "Default Organization"
                assert default_org.plan == "legacy"
                assert default_org.owner_user_id == admin_id
                default_org_id = default_org.id

                # ── Assert 4: every pre-existing tenant row was
                # backfilled with the default org id. ─────────────────
                for model in (
                    Site,
                    Group,
                    WatchdogRule,
                    Schedule,
                    Scene,
                    Invitation,
                    RoleBinding,
                ):
                    rows = list(s.scalars(sa.select(model)))
                    assert rows, f"no {model.__name__} rows seeded"
                    for row in rows:
                        assert row.organization_id == default_org_id, (
                            f"{model.__name__} row {row.id!r} not "
                            f"backfilled with the default org"
                        )

                # One membership per seeded user.
                memberships = {
                    m.user_id: m.org_role
                    for m in s.scalars(sa.select(OrganizationMembership))
                }
                assert memberships[admin_id] == "owner"
                assert memberships[operator_id] == "member"

        # ── Assert 5: a representative org-scoped query succeeds. ─────
        # Run a Tier-A SELECT inside the default org's tenant scope —
        # the do_orm_execute filter must apply cleanly and return the
        # backfilled rows. Pre-fix, the schema is broken before this is
        # ever reachable.
        with tenant_scope.org_context(default_org_id):
            with session_scope() as s:
                scoped_sites = list(s.scalars(sa.select(Site)))
                assert {site.id for site in scoped_sites} == {
                    site_a_id,
                    site_b_id,
                }
                for site in scoped_sites:
                    assert site.organization_id == default_org_id
