"""Unit tests — organization boundary phase 3 (constraint hardening).

Covers the schema-level constraint hardening shipped in phase 3
(design: docs/notes/2026-05-20-organization-boundary-design.md
§6.3, §2, §4.3) — exercised against the live ORM metadata and the
`hub_db_unscoped` isolated-SQLite schema:

  * NOT-NULL `organization_id` on the Tier-A tables that require it,
    and the two tables that keep it nullable (`audit_events`,
    `device_announcements`).
  * per-org unique constraints — `sites.name`, `groups.name`,
    `scenes.name` are unique per org, not globally: the same name may
    recur across orgs but collides within one.
  * the widened `uq_role_binding_scope` — now keyed by org.
  * FK on-delete behaviour — RESTRICT for the org-owned config
    entities, CASCADE for `invitations`.

The constraint behaviour is asserted under `tenant_scope.system()` so
the tests place rows in specific orgs deliberately; the runtime
read-filter is not the subject here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import get_engine, session_scope
from app.models import (
    Group,
    Organization,
    Scene,
    Site,
    User,
)
from app.services import tenant_scope


# ── helpers ────────────────────────────────────────────────────────────


def _two_orgs():
    with tenant_scope.system():
        with session_scope() as s:
            oa = Organization(name="Org A", slug="org-a")
            ob = Organization(name="Org B", slug="org-b")
            s.add_all([oa, ob])
            s.flush()
            return oa.id, ob.id


# ── NOT-NULL flip ──────────────────────────────────────────────────────

_NOT_NULL_ORG_TABLES = (
    "sites",
    "groups",
    "watchdog_rules",
    "schedules",
    "scenes",
    "enrollment_tokens",
    "external_sensor_sources",
    "role_bindings",
    "invitations",
)
_NULLABLE_ORG_TABLES = ("audit_events", "device_announcements")


@pytest.mark.parametrize("table", _NOT_NULL_ORG_TABLES)
def test_organization_id_is_not_null_in_schema(hub_db_unscoped, table):
    """The hardened schema has a NOT NULL organization_id on every
    org-required Tier-A table."""
    insp = inspect(get_engine())
    cols = {c["name"]: c for c in insp.get_columns(table)}
    assert cols["organization_id"]["nullable"] is False, table


@pytest.mark.parametrize("table", _NULLABLE_ORG_TABLES)
def test_organization_id_stays_nullable_where_designed(hub_db_unscoped, table):
    """`audit_events` and `device_announcements` keep a nullable
    organization_id (design §3.6, §2)."""
    insp = inspect(get_engine())
    cols = {c["name"]: c for c in insp.get_columns(table)}
    assert cols["organization_id"]["nullable"] is True, table


def test_site_insert_without_org_fails_in_system_context(hub_db_unscoped):
    """Inside system() the before_flush stamping is a no-op, so a Site
    with no organization_id hits the NOT NULL constraint at flush — the
    column is genuinely required at the DB level."""
    _two_orgs()
    with pytest.raises(IntegrityError):
        with tenant_scope.system():
            with session_scope() as s:
                s.add(Site(name="orphan"))  # no organization_id
                s.flush()


# ── per-org unique constraints ─────────────────────────────────────────


def test_site_name_unique_within_org(hub_db_unscoped):
    """Two sites with the same name in the SAME org collide."""
    org_a, _ = _two_orgs()
    with pytest.raises(IntegrityError):
        with tenant_scope.system():
            with session_scope() as s:
                s.add(Site(name="dup", organization_id=org_a))
                s.add(Site(name="dup", organization_id=org_a))
                s.flush()


def test_site_name_may_repeat_across_orgs(hub_db_unscoped):
    """The SAME site name in DIFFERENT orgs is allowed — `name` is
    unique per org, not globally (design §6.3)."""
    org_a, org_b = _two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            s.add(Site(name="HQ", organization_id=org_a))
            s.add(Site(name="HQ", organization_id=org_b))
            s.flush()
    with tenant_scope.system():
        with session_scope() as s:
            rows = list(s.scalars(select(Site).where(Site.name == "HQ")))
            assert len(rows) == 2
            assert {r.organization_id for r in rows} == {org_a, org_b}


def test_group_name_may_repeat_across_orgs(hub_db_unscoped):
    org_a, org_b = _two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            s.add(Group(name="Rack 1", organization_id=org_a))
            s.add(Group(name="Rack 1", organization_id=org_b))
            s.flush()


def test_group_name_unique_within_org(hub_db_unscoped):
    org_a, _ = _two_orgs()
    with pytest.raises(IntegrityError):
        with tenant_scope.system():
            with session_scope() as s:
                s.add(Group(name="dup", organization_id=org_a))
                s.add(Group(name="dup", organization_id=org_a))
                s.flush()


def test_scene_name_may_repeat_across_orgs(hub_db_unscoped):
    org_a, org_b = _two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            s.add(Scene(name="Movie Night", organization_id=org_a))
            s.add(Scene(name="Movie Night", organization_id=org_b))
            s.flush()


def test_scene_name_unique_within_org(hub_db_unscoped):
    org_a, _ = _two_orgs()
    with pytest.raises(IntegrityError):
        with tenant_scope.system():
            with session_scope() as s:
                s.add(Scene(name="dup", organization_id=org_a))
                s.add(Scene(name="dup", organization_id=org_a))
                s.flush()


def test_per_org_name_uniques_present_in_schema(hub_db_unscoped):
    """The schema carries the named per-org unique constraints, not the
    old global UNIQUE(name)."""
    insp = inspect(get_engine())
    for table in ("sites", "groups", "scenes"):
        uqs = {
            tuple(c["column_names"]) for c in insp.get_unique_constraints(table)
        }
        assert ("organization_id", "name") in uqs, table
        assert ("name",) not in uqs, table


# ── role_bindings uq widened by org (design §4.3) ──────────────────────


def test_role_binding_unique_constraint_includes_org(hub_db_unscoped):
    """uq_role_binding_scope is keyed by organization_id so the same
    (user, scope_type, scope_id) tuple can recur across orgs."""
    insp = inspect(get_engine())
    rb_uqs = {
        c["name"]: tuple(c["column_names"])
        for c in insp.get_unique_constraints("role_bindings")
    }
    assert rb_uqs.get("uq_role_binding_scope") == (
        "organization_id", "user_id", "scope_type", "scope_id",
    )


def test_same_binding_allowed_across_orgs(hub_db_unscoped):
    """A user with a site-scoped binding in org A may have an
    identically-shaped binding in org B — the org dimension keeps them
    distinct."""
    from app.models import RoleBinding

    org_a, org_b = _two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            u = User(email="msp@example.com", password_hash="x")
            s.add(u)
            s.flush()
            uid = u.id
            # same scope_type/scope_id (a global binding, scope_id NULL)
            # in two different orgs — must NOT collide.
            s.add(RoleBinding(
                organization_id=org_a, user_id=uid,
                scope_type="global", role="admin",
            ))
            s.add(RoleBinding(
                organization_id=org_b, user_id=uid,
                scope_type="global", role="admin",
            ))
            s.flush()


# ── FK on-delete behaviour ─────────────────────────────────────────────


def test_org_fk_ondelete_is_restrict_for_sites(hub_db_unscoped):
    """sites.organization_id FK is ON DELETE RESTRICT — an accidental
    org delete with sites still attached fails loudly (design §2)."""
    insp = inspect(get_engine())
    fks = [
        f for f in insp.get_foreign_keys("sites")
        if "organization_id" in f["constrained_columns"]
    ]
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "organizations"
    assert fks[0]["options"].get("ondelete", "").upper() == "RESTRICT"


def test_org_fk_ondelete_is_cascade_for_invitations(hub_db_unscoped):
    """invitations.organization_id FK is ON DELETE CASCADE — an invite
    is meaningless once its org is gone (design §2)."""
    insp = inspect(get_engine())
    fks = [
        f for f in insp.get_foreign_keys("invitations")
        if "organization_id" in f["constrained_columns"]
    ]
    assert len(fks) == 1
    assert fks[0]["options"].get("ondelete", "").upper() == "CASCADE"


@pytest.mark.parametrize(
    "table",
    ("groups", "watchdog_rules", "schedules", "scenes",
     "enrollment_tokens", "external_sensor_sources", "role_bindings"),
)
def test_org_fk_ondelete_is_restrict_for_config_tables(hub_db_unscoped, table):
    """Every org-owned config table uses ON DELETE RESTRICT (design §2)."""
    insp = inspect(get_engine())
    fks = [
        f for f in insp.get_foreign_keys(table)
        if "organization_id" in f["constrained_columns"]
    ]
    assert len(fks) == 1, table
    assert fks[0]["options"].get("ondelete", "").upper() == "RESTRICT", table
