"""Unit tests — organization boundary phase 1.

Covers the additive foundation of the multi-tenant `organization`
boundary (design: docs/notes/2026-05-20-organization-boundary-design.md):

  * the `Organization` and `OrganizationMembership` models,
  * the `TenantScoped` mixin marking the 10 Tier-A models,
  * the idempotent `ensure_default_organization_backfill()`.

Phase 1 is additive only — there is no isolation enforcement to test
yet. DB-backed cases use the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import (
    AuditEvent,
    DeviceAnnouncement,
    EnrollmentToken,
    Group,
    Invitation,
    Organization,
    OrganizationMembership,
    RoleBinding,
    Scene,
    Schedule,
    Site,
    User,
    WatchdogRule,
)
from app.models.external_sensors import ExternalSensorSource
from app.services.bootstrap import (
    _ORG_BACKFILL_KEY,
    _ORG_TIER_A_TABLES,
    ensure_default_organization_backfill,
)
from app.services.tenant_scope import TenantScoped


# ── TenantScoped mixin (static) ────────────────────────────────────────

# The Tier-A models the design lists in section 2. Phase 1 marked 10;
# org-boundary phase 2 added `DeviceAnnouncement` (design §2 lists it as
# Tier-A — phase 1's table list omitted it).
_TIER_A_MODELS = (
    Site,
    Group,
    WatchdogRule,
    Schedule,
    Scene,
    EnrollmentToken,
    ExternalSensorSource,
    RoleBinding,
    Invitation,
    AuditEvent,
    DeviceAnnouncement,
)


@pytest.mark.parametrize("model", _TIER_A_MODELS, ids=lambda m: m.__name__)
def test_tier_a_model_is_tenant_scoped(model):
    """Every Tier-A model mixes in TenantScoped and carries a nullable
    organization_id column."""
    assert issubclass(model, TenantScoped)
    col = model.__table__.c.organization_id
    assert col is not None
    assert col.nullable is True  # phase 1 — nullable only, no enforcement


def test_tenant_scoped_organization_id_is_fk_to_organizations():
    fks = list(Site.__table__.c.organization_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "organizations"


def test_non_tier_a_models_are_not_tenant_scoped():
    """Tier-B / platform-global models must NOT carry the mixin."""
    from app.models import Device, User as _User
    from app.models.firmware import FirmwareRelease

    assert not issubclass(Device, TenantScoped)       # org derived via site
    assert not issubclass(_User, TenantScoped)        # M:N via join table
    assert not issubclass(FirmwareRelease, TenantScoped)  # platform-global


def test_backfill_table_list_matches_tier_a_models():
    """The backfill's table list must cover exactly the Tier-A tables."""
    model_tables = {m.__table__.name for m in _TIER_A_MODELS}
    assert set(_ORG_TIER_A_TABLES) == model_tables


# ── Organization / OrganizationMembership models ───────────────────────

def test_create_organization(hub_db):
    with session_scope() as s:
        org = Organization(name="Acme", slug="acme")
        s.add(org)
        s.flush()
        assert org.id.startswith("org_")
        # defaults applied
        assert org.status == "active"
        assert org.plan == "free"
        assert org.is_self_hosted_default is False


def test_organization_slug_is_unique(hub_db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(Organization(name="One", slug="dup"))
            s.add(Organization(name="Two", slug="dup"))


def test_organization_name_is_not_unique(hub_db):
    """name is a per-org display string — two orgs may share a name."""
    with session_scope() as s:
        s.add(Organization(name="Same Name", slug="slug-a"))
        s.add(Organization(name="Same Name", slug="slug-b"))
    with session_scope() as s:
        count = s.scalar(
            select(func.count())
            .select_from(Organization)
            .where(Organization.name == "Same Name")
        )
        assert count == 2


def test_create_organization_membership(hub_db):
    with session_scope() as s:
        org = Organization(name="Acme", slug="acme")
        user = User(email="u@example.com", password_hash="x")
        s.add_all([org, user])
        s.flush()
        m = OrganizationMembership(
            organization_id=org.id, user_id=user.id, org_role="owner"
        )
        s.add(m)
        s.flush()
        assert m.id.startswith("om_")
        assert m.org_role == "owner"


def test_organization_membership_is_unique_per_org_user(hub_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as s:
        org = Organization(name="Acme", slug="acme")
        user = User(email="u@example.com", password_hash="x")
        s.add_all([org, user])
        s.flush()
        org_id, user_id = org.id, user.id

    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(OrganizationMembership(organization_id=org_id, user_id=user_id))
            s.add(OrganizationMembership(organization_id=org_id, user_id=user_id))


# ── ensure_default_organization_backfill ───────────────────────────────

def _seed_pre_org_world():
    """Seed a pre-multi-tenant database: a super-admin, an ordinary
    user, and one row in each of several Tier-A tables — all with
    organization_id NULL."""
    with session_scope() as s:
        admin = User(
            email="admin@example.com",
            password_hash="x",
            role="super_admin",
            is_super_admin=True,
        )
        member = User(
            email="member@example.com",
            password_hash="x",
            role="operator",
            is_super_admin=False,
        )
        s.add_all([admin, member])
        s.flush()
        site = Site(name="HQ")
        s.add(site)
        s.flush()
        s.add(Group(name="Rack 1", site_id=site.id))
        s.add(WatchdogRule(name="ping gw"))
        s.add(Scene(name="Movie Night"))
        s.add(RoleBinding(user_id=admin.id, scope_type="global", role="admin"))
        return admin.id, member.id


def test_backfill_creates_default_org_and_stamps_rows(hub_db):
    admin_id, member_id = _seed_pre_org_world()

    ensure_default_organization_backfill()

    with session_scope() as s:
        orgs = list(s.scalars(select(Organization)))
        assert len(orgs) == 1
        org = orgs[0]
        assert org.slug == "default"
        assert org.name == "Default Organization"
        assert org.plan == "legacy"
        assert org.status == "active"
        # owner is the super-admin
        assert org.owner_user_id == admin_id

        # every Tier-A row is stamped with the default org
        for model in (Site, Group, WatchdogRule, Scene, RoleBinding):
            rows = list(s.scalars(select(model)))
            assert rows, model.__name__
            for r in rows:
                assert r.organization_id == org.id, model.__name__

        # one membership per user, owner mapped correctly
        memberships = {
            m.user_id: m.org_role
            for m in s.scalars(select(OrganizationMembership))
        }
        assert memberships[admin_id] == "owner"
        assert memberships[member_id] == "member"


def test_backfill_is_idempotent(hub_db):
    _seed_pre_org_world()

    ensure_default_organization_backfill()
    ensure_default_organization_backfill()  # second run must be a no-op

    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Organization)) == 1
        # still exactly one membership per user (2 users seeded)
        assert (
            s.scalar(
                select(func.count()).select_from(OrganizationMembership)
            )
            == 2
        )


def test_backfill_skips_when_an_org_already_exists(hub_db):
    """If organizations is non-empty, the backfill must not create
    another org or stamp rows — it just marks itself complete."""
    with session_scope() as s:
        s.add(Organization(name="Pre-existing", slug="pre"))
    _seed_pre_org_world()

    ensure_default_organization_backfill()

    with session_scope() as s:
        # no new org created
        assert s.scalar(select(func.count()).select_from(Organization)) == 1
        # rows were NOT stamped (the backfill bailed early)
        site = s.scalar(select(Site))
        assert site.organization_id is None
        # no memberships created
        assert (
            s.scalar(
                select(func.count()).select_from(OrganizationMembership)
            )
            == 0
        )


def test_backfill_marks_tracking_key(hub_db):
    from app.services import runtime_settings as rs

    _seed_pre_org_world()
    assert rs.has_db_value(_ORG_BACKFILL_KEY) is False
    ensure_default_organization_backfill()
    assert rs.has_db_value(_ORG_BACKFILL_KEY) is True


def test_backfill_ownerless_when_no_super_admin(hub_db):
    """An install with no super-admin gets an ownerless default org —
    acceptable, the FK is nullable."""
    with session_scope() as s:
        s.add(
            User(
                email="plain@example.com",
                password_hash="x",
                role="operator",
                is_super_admin=False,
            )
        )

    ensure_default_organization_backfill()

    with session_scope() as s:
        org = s.scalar(select(Organization))
        assert org is not None
        assert org.owner_user_id is None
