"""Unit tests — organization boundary phase 2 (tenant-isolation enforcement).

Covers the runtime enforcement mechanism shipped in phase 2 (design:
docs/notes/2026-05-20-organization-boundary-design.md section 3):

  * the do_orm_execute read filter — with org-A context active, org-B's
    Tier-A rows are NOT returned (enforce mode); SHADOW mode is log-only.
  * the before_flush write-stamping — new Tier-A rows are stamped with
    the active org; cross-org writes are caught.
  * the tenant_scope.system() explicit bypass — works for the
    device-API / background-job paths.
  * the org ContextVar resets cleanly between requests (no pooled-worker
    leakage).
  * resolve_active_org() never trusts a session value blind.

SHADOW MODE IS THE DEFAULT. Tests that exercise hard enforcement
explicitly flip `org_isolation.enforce` to "enforce"; tests that assert
the default behaviour leave it untouched (or set "shadow").

All cases use the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import (
    DeviceAnnouncement,
    Group,
    Organization,
    RoleBinding,
    Scene,
    Schedule,
    Site,
    User,
    WatchdogRule,
)
from app.models.external_sensors import ExternalSensorSource
from app.services import org_membership, runtime_settings, tenant_scope


# ── helpers ────────────────────────────────────────────────────────────


def _enforce():
    """Flip the runtime toggle to hard-enforce mode for the current test."""
    runtime_settings.set_(tenant_scope.ORG_ENFORCE_MODE_KEY, "enforce")


def _shadow():
    """Explicitly select shadow mode (the default)."""
    runtime_settings.set_(tenant_scope.ORG_ENFORCE_MODE_KEY, "shadow")


def _seed_two_orgs():
    """Create org A and org B, each with one row in every representative
    Tier-A table. Returns (org_a_id, org_b_id)."""
    with tenant_scope.system():
        with session_scope() as s:
            oa = Organization(name="Org A", slug="org-a")
            ob = Organization(name="Org B", slug="org-b")
            s.add_all([oa, ob])
            s.flush()
            org_a, org_b = oa.id, ob.id

    for org_id, tag in ((org_a, "a"), (org_b, "b")):
        with tenant_scope.org_context(org_id):
            with session_scope() as s:
                site = Site(name=f"site-{tag}")
                s.add(site)
                s.flush()
                s.add(Group(name=f"group-{tag}", site_id=site.id))
                s.add(WatchdogRule(name=f"rule-{tag}"))
                s.add(Schedule(name=f"sched-{tag}", kind="recurring"))
                s.add(Scene(name=f"scene-{tag}"))
                s.add(
                    ExternalSensorSource(
                        kind="roku",
                        display_name=f"sensor-{tag}",
                        host=f"10.0.0.{1 if tag == 'a' else 2}",
                    )
                )
                s.add(
                    DeviceAnnouncement(
                        mac_address=f"AA:BB:CC:00:00:0{0 if tag == 'a' else 1}"
                    )
                )
    return org_a, org_b


# ── read filter — cross-tenant isolation (the core security property) ──

# Every representative Tier-A model. The filter is global
# (with_loader_criteria(TenantScoped, ...)) so all of them are covered at
# once — this parametrize proves it per-entity.
_TIER_A_QUERY_MODELS = (
    Site,
    Group,
    WatchdogRule,
    Schedule,
    Scene,
    ExternalSensorSource,
    DeviceAnnouncement,
)


@pytest.mark.parametrize("model", _TIER_A_QUERY_MODELS, ids=lambda m: m.__name__)
def test_enforce_mode_hides_other_orgs_rows(hub_db, model):
    """ENFORCE: with org-A context active, a SELECT of a Tier-A entity
    returns ONLY org-A rows — org-B's rows are structurally invisible."""
    org_a, org_b = _seed_two_orgs()
    _enforce()

    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            rows = list(s.scalars(select(model)))
            assert len(rows) == 1, f"{model.__name__}: expected only org-A row"
            assert rows[0].organization_id == org_a

    with tenant_scope.org_context(org_b):
        with session_scope() as s:
            rows = list(s.scalars(select(model)))
            assert len(rows) == 1
            assert rows[0].organization_id == org_b


def test_enforce_mode_get_by_id_cannot_cross_org(hub_db):
    """ENFORCE: fetching org-B's Site row by its primary key from inside
    org-A context returns None — a direct-id probe cannot cross orgs."""
    org_a, org_b = _seed_two_orgs()
    _enforce()

    with tenant_scope.org_context(org_b):
        with session_scope() as s:
            b_site_id = s.scalar(select(Site.id))

    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            # session.get() also flows through do_orm_execute.
            assert s.get(Site, b_site_id) is None
            # and an explicit where-by-id likewise yields nothing
            assert (
                s.scalar(select(Site).where(Site.id == b_site_id)) is None
            )


def test_enforce_mode_count_is_org_scoped(hub_db):
    """ENFORCE: an aggregate COUNT is filtered too — org A sees count 1,
    not 2, even though two sites exist."""
    org_a, _ = _seed_two_orgs()
    _enforce()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(Site)) == 1


def test_shadow_mode_does_not_filter(hub_db):
    """SHADOW (the default): the read filter is count-and-log only — org
    A still sees org B's rows. This proves the safe default does NOT
    silently change behaviour before the enforce flip."""
    org_a, _ = _seed_two_orgs()
    _shadow()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(Site)) == 2


def test_default_mode_is_shadow(hub_db):
    """With NO runtime setting written at all, the mode is shadow — hard
    enforcement is never on by default."""
    assert tenant_scope.enforce_mode() == tenant_scope.ENFORCE_MODE_SHADOW
    assert tenant_scope.is_enforcing() is False
    org_a, _ = _seed_two_orgs()
    # no _enforce() / _shadow() call — pure default
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(Site)) == 2


def test_non_tier_a_query_is_never_filtered(hub_db):
    """A query against a non-TenantScoped model (User) is untouched by
    the filter even under an active org scope and enforce mode."""
    with tenant_scope.system():
        with session_scope() as s:
            s.add_all(
                [
                    User(email="u1@example.com", password_hash="x"),
                    User(email="u2@example.com", password_hash="x"),
                ]
            )
    org_a, _ = _seed_two_orgs()
    _enforce()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(User)) == 2


# ── system() bypass ────────────────────────────────────────────────────


def test_system_bypass_sees_all_orgs(hub_db):
    """tenant_scope.system() — the device-API / background-job path —
    sees every org's rows even in enforce mode."""
    _seed_two_orgs()
    _enforce()
    with tenant_scope.system():
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(Site)) == 2


def test_system_bypass_clears_inherited_org(hub_db):
    """A system() block opened INSIDE an org context is genuinely
    unscoped — the inherited org is cleared, not silently still applied."""
    org_a, _ = _seed_two_orgs()
    _enforce()
    with tenant_scope.org_context(org_a):
        assert tenant_scope.current_org() == org_a
        with tenant_scope.system():
            assert tenant_scope.in_system_context() is True
            assert tenant_scope.current_org() is None
            with session_scope() as s:
                assert s.scalar(select(func.count()).select_from(Site)) == 2
        # restored on exit
        assert tenant_scope.current_org() == org_a
        assert tenant_scope.in_system_context() is False


def test_org_context_nested_restores_previous(hub_db):
    """org_context() restores the previous scope on exit — used by jobs
    that iterate orgs."""
    org_a, org_b = _seed_two_orgs()
    with tenant_scope.org_context(org_a):
        assert tenant_scope.current_org() == org_a
        with tenant_scope.org_context(org_b):
            assert tenant_scope.current_org() == org_b
        assert tenant_scope.current_org() == org_a
    assert tenant_scope.current_org() is None


# ── before_flush write-stamping & cross-org write guard ────────────────


def test_insert_is_stamped_with_active_org(hub_db):
    """A new Tier-A row created with no organization_id set is stamped
    from the active org by before_flush — 'service code forgot to set the
    org' becomes a guaranteed-correct default."""
    org_a, _ = _seed_two_orgs()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            site = Site(name="unstamped")  # no organization_id
            s.add(site)
            s.flush()
            assert site.organization_id == org_a


def test_cross_org_insert_rejected_in_enforce_mode(hub_db):
    """ENFORCE: inserting a Tier-A row carrying a DIFFERENT org than the
    active scope raises CrossOrgWriteError and aborts the flush."""
    org_a, org_b = _seed_two_orgs()
    _enforce()
    with pytest.raises(tenant_scope.CrossOrgWriteError):
        with tenant_scope.org_context(org_a):
            with session_scope() as s:
                s.add(Site(name="evil", organization_id=org_b))
                s.flush()
    # the row must not have been committed
    with tenant_scope.system():
        with session_scope() as s:
            assert s.scalar(
                select(func.count())
                .select_from(Site)
                .where(Site.name == "evil")
            ) == 0


def test_cross_org_insert_allowed_but_logged_in_shadow_mode(hub_db):
    """SHADOW (default): a cross-org write is NOT blocked — it proceeds
    (legacy behaviour) and is logged as a tenant.shadow_write audit row
    for the pre-enforce review."""
    org_a, org_b = _seed_two_orgs()
    _shadow()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            s.add(Site(name="shadow-cross", organization_id=org_b))
            s.flush()
    # the row WAS written, with its foreign org intact
    with tenant_scope.system():
        with session_scope() as s:
            row = s.scalar(select(Site).where(Site.name == "shadow-cross"))
            assert row is not None
            assert row.organization_id == org_b
    # and an audit row records the divergence
    _assert_audit_action_present(tenant_scope.AUDIT_SHADOW_WRITE)


def test_cross_org_update_rejected_in_enforce_mode(hub_db):
    """ENFORCE: re-homing an existing Tier-A row to another org via an
    UPDATE is caught — a row cannot be moved across the tenant boundary."""
    org_a, org_b = _seed_two_orgs()
    _enforce()
    with tenant_scope.system():
        with session_scope() as s:
            a_site_id = s.scalar(select(Site.id).where(Site.organization_id == org_a))

    with pytest.raises(tenant_scope.CrossOrgWriteError):
        with tenant_scope.org_context(org_a):
            with session_scope() as s:
                site = s.get(Site, a_site_id)
                site.organization_id = org_b  # attempt to re-home
                s.flush()


def test_system_context_write_keeps_explicit_org(hub_db):
    """Inside system() the write-stamping is a no-op — system code is
    trusted to set whatever org it needs (e.g. the backfill)."""
    org_a, org_b = _seed_two_orgs()
    _enforce()
    with tenant_scope.system():
        with session_scope() as s:
            s.add(Site(name="sys-b", organization_id=org_b))
            s.flush()
    with tenant_scope.system():
        with session_scope() as s:
            row = s.scalar(select(Site).where(Site.name == "sys-b"))
            assert row.organization_id == org_b


# ── unscoped-access detection (the latent-bug control) ─────────────────


def test_unscoped_tier_a_select_emits_audit(hub_db):
    """A Tier-A SELECT with NO org bound and NO system bypass is a latent
    isolation bug — it emits a tenant.unscoped_access audit row. The
    query itself is NOT blocked (a None org with no bypass is a code bug,
    not an attacker — failing closed would just be an outage)."""
    _seed_two_orgs()
    # deliberately neither set_org() nor system()
    assert tenant_scope.current_org() is None
    assert tenant_scope.in_system_context() is False
    with session_scope() as s:
        rows = list(s.scalars(select(Site)))
        assert len(rows) == 2  # not blocked
    _assert_audit_action_present(tenant_scope.AUDIT_UNSCOPED)


def _assert_audit_action_present(action: str):
    """Helper — assert at least one audit_events row with `action`."""
    from app.models import AuditEvent

    with tenant_scope.system():
        with session_scope() as s:
            n = s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == action)
            )
    assert n and n >= 1, f"expected an audit row with action={action!r}"


# ── ContextVar reset / no pooled-worker leakage ────────────────────────


def test_reset_clears_the_scope(hub_db):
    """tenant_scope.reset() returns the ContextVar to the unset state."""
    org_a, _ = _seed_two_orgs()
    tenant_scope.set_org(org_a)
    assert tenant_scope.current_org() == org_a
    tenant_scope.reset()
    assert tenant_scope.current_org() is None


def test_teardown_resets_even_after_exception(hub_db):
    """The teardown_request hook must clear the scope even when the view
    raised — otherwise the next request on the pooled worker leaks the
    previous tenant. Simulate: bind an org, run a 'request' that raises,
    confirm the teardown hook cleared it."""
    import flask

    from app.middleware.admin_auth import register_tenant_teardown

    org_a, org_b = _seed_two_orgs()

    app = flask.Flask(__name__)
    register_tenant_teardown(app)

    @app.route("/boom")
    def _boom():
        # Simulate role_required_* binding the tenant, then the view
        # blowing up mid-request.
        tenant_scope.set_org(org_a)
        raise RuntimeError("view exploded")

    client = app.test_client()
    # the request 500s — but the teardown must still fire
    client.get("/boom")

    # After the request, the worker's ContextVar must be clean. If the
    # teardown had not run, current_org() would still be org_a here.
    assert tenant_scope.current_org() is None


def test_request_does_not_leak_org_into_next_request(hub_db):
    """Two sequential 'requests': the first binds org A, the second binds
    nothing — the second must NOT see org A's scope."""
    import flask

    from app.middleware.admin_auth import register_tenant_teardown

    org_a, _ = _seed_two_orgs()
    app = flask.Flask(__name__)
    register_tenant_teardown(app)

    seen = {}

    @app.route("/first")
    def _first():
        tenant_scope.set_org(org_a)
        seen["first"] = tenant_scope.current_org()
        return "ok"

    @app.route("/second")
    def _second():
        # This handler binds nothing — it must start from a clean scope.
        seen["second_entry"] = tenant_scope.current_org()
        return "ok"

    client = app.test_client()
    client.get("/first")
    client.get("/second")
    assert seen["first"] == org_a
    assert seen["second_entry"] is None  # no leak from request 1


# ── resolve_active_org — never trust the session blind ─────────────────


def _seed_user_in_org(org_id: str, email: str, org_role: str = "member"):
    from app.models import OrganizationMembership

    with tenant_scope.system():
        with session_scope() as s:
            u = User(email=email, password_hash="x")
            s.add(u)
            s.flush()
            s.add(
                OrganizationMembership(
                    organization_id=org_id, user_id=u.id, org_role=org_role
                )
            )
            return u.id


def test_resolve_active_org_defaults_to_sole_membership(hub_db):
    org_a, _ = _seed_two_orgs()
    uid = _seed_user_in_org(org_a, "solo@example.com")

    class _U:
        id = uid

    assert org_membership.resolve_active_org(_U()) == org_a


def test_resolve_active_org_honours_valid_session_value(hub_db):
    """A session active_org_id the user IS a member of is honoured."""
    org_a, org_b = _seed_two_orgs()
    uid = _seed_user_in_org(org_a, "msp@example.com")
    # also a member of org B
    from app.models import OrganizationMembership

    with tenant_scope.system():
        with session_scope() as s:
            s.add(
                OrganizationMembership(
                    organization_id=org_b, user_id=uid, org_role="member"
                )
            )

    class _U:
        id = uid

    assert (
        org_membership.resolve_active_org(_U(), session_org_id=org_b) == org_b
    )


def test_resolve_active_org_rejects_foreign_session_value(hub_db):
    """A session active_org_id pointing at an org the user is NOT a
    member of is rejected — it never widens access. Falls back to the
    user's real membership."""
    org_a, org_b = _seed_two_orgs()
    uid = _seed_user_in_org(org_a, "victim@example.com")

    class _U:
        id = uid

    # attacker-tampered session value naming org B
    resolved = org_membership.resolve_active_org(_U(), session_org_id=org_b)
    assert resolved == org_a  # NOT org_b — the foreign value was ignored


def test_resolve_active_org_none_when_no_membership(hub_db):
    """A user with no membership resolves to None — the request then runs
    unscoped and any Tier-A access is flagged, rather than silently
    granted ambient cross-org reach."""
    _seed_two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            u = User(email="orphan@example.com", password_hash="x")
            s.add(u)
            s.flush()
            uid = u.id

    class _U:
        id = uid

    assert org_membership.resolve_active_org(_U()) is None


def test_memberships_for_user_orders_owner_first(hub_db):
    """memberships_for_user() puts owner memberships first so the
    primary-org default is deterministic for MSP users."""
    org_a, org_b = _seed_two_orgs()
    from app.models import OrganizationMembership

    with tenant_scope.system():
        with session_scope() as s:
            u = User(email="multi@example.com", password_hash="x")
            s.add(u)
            s.flush()
            uid = u.id
            # member of A, owner of B
            s.add(
                OrganizationMembership(
                    organization_id=org_a, user_id=uid, org_role="member"
                )
            )
            s.add(
                OrganizationMembership(
                    organization_id=org_b, user_id=uid, org_role="owner"
                )
            )

    rows = org_membership.memberships_for_user(uid)
    assert len(rows) == 2
    assert rows[0]["org_role"] == "owner"
    assert rows[0]["organization_id"] == org_b


# ── enforce_mode toggle ────────────────────────────────────────────────


def test_enforce_mode_toggle_is_runtime(hub_db):
    """The shadow<->enforce switch is a single runtime setting — no
    redeploy. Flipping it changes enforce_mode() immediately."""
    assert tenant_scope.enforce_mode() == "shadow"
    runtime_settings.set_(tenant_scope.ORG_ENFORCE_MODE_KEY, "enforce")
    assert tenant_scope.enforce_mode() == "enforce"
    runtime_settings.set_(tenant_scope.ORG_ENFORCE_MODE_KEY, "shadow")
    assert tenant_scope.enforce_mode() == "shadow"
    # an unrecognised value falls back to the SAFE default (shadow)
    runtime_settings.set_(tenant_scope.ORG_ENFORCE_MODE_KEY, "garbage")
    assert tenant_scope.enforce_mode() == "shadow"


# ── RBAC org-awareness (design section 4.2) ────────────────────────────


def test_role_bindings_select_is_org_scoped_in_enforce_mode(hub_db):
    """ENFORCE: RoleBinding is TenantScoped — a select(RoleBinding) from
    org-A context returns only org-A bindings. This is what makes
    scope_type='global' mean 'global within this org'."""
    org_a, org_b = _seed_two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            ua = User(email="a-admin@example.com", password_hash="x")
            ub = User(email="b-admin@example.com", password_hash="x")
            s.add_all([ua, ub])
            s.flush()
            ua_id, ub_id = ua.id, ub.id
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            s.add(RoleBinding(user_id=ua_id, scope_type="global", role="admin"))
    with tenant_scope.org_context(org_b):
        with session_scope() as s:
            s.add(RoleBinding(user_id=ub_id, scope_type="global", role="admin"))

    _enforce()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            bindings = list(s.scalars(select(RoleBinding)))
            assert len(bindings) == 1
            assert bindings[0].user_id == ua_id  # org A's binding only


def test_binding_scope_belongs_to_org_rejects_foreign_site(hub_db):
    """The design section 4.2 defense-in-depth check: a site binding whose
    scope_id points at another org's site does not 'belong'."""
    from app.services.role_bindings import binding_scope_belongs_to_org

    org_a, org_b = _seed_two_orgs()
    with tenant_scope.system():
        with session_scope() as s:
            a_site = s.scalar(select(Site.id).where(Site.organization_id == org_a))
            b_site = s.scalar(select(Site.id).where(Site.organization_id == org_b))

    assert binding_scope_belongs_to_org("site", a_site, org_a) is True
    assert binding_scope_belongs_to_org("site", b_site, org_a) is False
    # a global binding has no scoped resource — always 'belongs'
    assert binding_scope_belongs_to_org("global", None, org_a) is True
