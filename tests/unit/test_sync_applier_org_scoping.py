"""Unit tests — organization boundary phase 3 (sync-applier org-scoping).

Covers the multi-hub sync-applier isolation fix shipped in phase 3
(design: docs/notes/2026-05-20-organization-boundary-design.md §3.7).

Multi-hub sync crosses a tenant trust boundary by design — a buggy or
hostile peer could send an outbox event that, applied naively, would
inject a row into the wrong org (or into the unscoped void). Phase 3
closes that gap:

  * `emit_outbox_event` stamps `scope_claims["organization_id"]` for an
    org-attributable entity (derived from the payload / its site).
  * `apply_outbox_event` reads that claim, REFUSES an event whose org
    does not exist locally (`UnknownOrgError`), and applies the row
    inside `tenant_scope.org_context(<org>)` so the before_flush
    write-stamping verifies/stamps every written row.
  * a Tier-A (`site`) payload whose body `organization_id` disagrees
    with the scope claim is pinned to the verified claim.

All cases use the `hub_db_unscoped` isolated-SQLite fixture (the post-phase-3
schema — `sites.organization_id` is NOT NULL).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import Organization, Site
from app.models.sync import OutboxEvent
from app.services import sync as sync_svc
from app.services import tenant_scope

T0 = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


# ── helpers ────────────────────────────────────────────────────────────


def _two_orgs():
    """Create org A and org B; return (org_a_id, org_b_id)."""
    with tenant_scope.system():
        with session_scope() as s:
            oa = Organization(name="Org A", slug="org-a")
            ob = Organization(name="Org B", slug="org-b")
            s.add_all([oa, ob])
            s.flush()
            return oa.id, ob.id


def _site_event(
    site_id: str,
    name: str,
    org_id: str | None,
    *,
    seq: int = 1,
    claims_org: str | None = "__derive__",
    payload_org: str | None = "__same__",
    at: datetime = T0,
) -> OutboxEvent:
    """Build a transient `site.created` OutboxEvent as the replicator
    would from peer JSON.

    `payload_org` — the `organization_id` inside the row body; defaults
    to `org_id`. `claims_org` — the org inside `scope_claims`; defaults
    to `org_id`. Passing them apart lets a test simulate a peer whose
    claim and body disagree, or a pre-phase-3 peer with no claim.
    """
    body_org = org_id if payload_org == "__same__" else payload_org
    claim_org = org_id if claims_org == "__derive__" else claims_org
    payload = {
        "id": site_id,
        "name": name,
        "organization_id": body_org,
        "description": None,
        "created_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }
    return OutboxEvent(
        seq=seq,
        at=at.isoformat(),
        event_type="site.created",
        entity_type="site",
        entity_id=site_id,
        payload=payload,
        scope_claims={"organization_id": claim_org} if claim_org else None,
    )


# ── emission: scope_claims carries organization_id ─────────────────────


def test_emit_stamps_org_into_scope_claims_for_site(hub_db_unscoped):
    """A `site` outbox event is emitted with the site's org in
    scope_claims — the Tier-A payload carries its own organization_id."""
    org_a, _ = _two_orgs()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            site = Site(name="hq")
            s.add(site)
            s.flush()
            ev = sync_svc.emit_outbox_event(
                s, "site.created", "site", site.id,
                sync_svc.entity_to_dict(site),
            )
            assert ev.scope_claims is not None
            assert ev.scope_claims["organization_id"] == org_a


def test_emit_caller_supplied_org_claim_is_kept(hub_db_unscoped):
    """A caller-supplied scope_claims org is not overwritten by the
    derivation."""
    org_a, _ = _two_orgs()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            site = Site(name="hq")
            s.add(site)
            s.flush()
            ev = sync_svc.emit_outbox_event(
                s, "site.created", "site", site.id,
                sync_svc.entity_to_dict(site),
                scope_claims={"organization_id": org_a, "extra": "kept"},
            )
            assert ev.scope_claims["organization_id"] == org_a
            assert ev.scope_claims["extra"] == "kept"


# ── applier: refuse an event for an unknown org ────────────────────────


def test_apply_refuses_event_for_unknown_org(hub_db_unscoped):
    """A hostile/buggy peer sends a site event claiming an org that does
    not exist locally — the applier raises UnknownOrgError and writes
    nothing."""
    _two_orgs()
    ev = _site_event("site_evil", "smuggled", "org_doesnotexist")
    with pytest.raises(sync_svc.UnknownOrgError):
        with session_scope() as s:
            sync_svc.apply_outbox_event(s, ev)
    # nothing was written
    with tenant_scope.system():
        with session_scope() as s:
            assert s.scalar(
                select(func.count()).select_from(Site)
                .where(Site.id == "site_evil")
            ) == 0


def test_apply_accepts_event_for_known_org(hub_db_unscoped):
    """A site event for an org that DOES exist locally is applied and
    lands in that org."""
    org_a, _ = _two_orgs()
    ev = _site_event("site_ok", "branch", org_a)
    with session_scope() as s:
        applied = sync_svc.apply_outbox_event(s, ev)
        assert applied is True
    with tenant_scope.system():
        with session_scope() as s:
            row = s.get(Site, "site_ok")
            assert row is not None
            assert row.organization_id == org_a


# ── applier: payload/claim disagreement is pinned to the claim ─────────


def test_apply_pins_site_org_to_scope_claim(hub_db_unscoped):
    """A peer sends a site whose body organization_id (org B) disagrees
    with its scope_claims org (org A). The verified scope claim wins —
    the applied row lands in org A, NOT org B."""
    org_a, org_b = _two_orgs()
    ev = _site_event(
        "site_pinned", "disputed", org_a,
        claims_org=org_a, payload_org=org_b,
    )
    with session_scope() as s:
        assert sync_svc.apply_outbox_event(s, ev) is True
    with tenant_scope.system():
        with session_scope() as s:
            row = s.get(Site, "site_pinned")
            assert row is not None
            # pinned to the scope-claim org, not the row body's org
            assert row.organization_id == org_a


def test_apply_runs_under_event_org_scope_in_enforce_mode(hub_db_unscoped):
    """The apply runs inside org_context(<event org>): with enforce mode
    on, a site event for org B is applied correctly into org B even
    though org B is otherwise invisible from an org-A context."""
    org_a, org_b = _two_orgs()
    from app.services import runtime_settings

    runtime_settings.set_(tenant_scope.ORG_ENFORCE_MODE_KEY, "enforce")

    ev = _site_event("site_b", "b-site", org_b)
    with session_scope() as s:
        assert sync_svc.apply_outbox_event(s, ev) is True

    # the row is in org B
    with tenant_scope.org_context(org_b):
        with session_scope() as s:
            assert s.get(Site, "site_b") is not None
    # and is NOT visible from org A (enforce-mode filter)
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            assert s.get(Site, "site_b") is None


# ── applier: pre-phase-3 peer (no org claim) still applies ─────────────


def test_apply_event_with_no_org_claim_uses_system_path(hub_db_unscoped):
    """An event with no scope_claims org — a `user` event, or an older
    peer build — is applied under the system bypass, unchanged. It must
    not raise UnknownOrgError (there is no org to verify)."""
    org_a, _ = _two_orgs()
    # a site event but with the org claim stripped (pre-phase-3 peer);
    # the body still carries a valid org so the NOT NULL column holds.
    ev = _site_event(
        "site_legacy", "legacy-peer", org_a, claims_org=None,
    )
    with session_scope() as s:
        assert sync_svc.apply_outbox_event(s, ev) is True
    with tenant_scope.system():
        with session_scope() as s:
            assert s.get(Site, "site_legacy") is not None


# ── applier: idempotency holds under org-scoping ───────────────────────


def test_apply_is_idempotent_under_org_scoping(hub_db_unscoped):
    """Re-applying the same scoped event is a no-op (last-writer-wins
    skips the second apply) — org-scoping does not break idempotency."""
    org_a, _ = _two_orgs()
    ev1 = _site_event("site_idem", "idem", org_a, seq=1)
    with session_scope() as s:
        assert sync_svc.apply_outbox_event(s, ev1) is True
    # identical event again — LWW sees updated_at already equal -> skip
    ev2 = _site_event("site_idem", "idem", org_a, seq=2)
    with session_scope() as s:
        assert sync_svc.apply_outbox_event(s, ev2) is False
    with tenant_scope.system():
        with session_scope() as s:
            assert s.scalar(
                select(func.count()).select_from(Site)
                .where(Site.id == "site_idem")
            ) == 1


# ── org_id_from_payload helper ─────────────────────────────────────────


def test_org_id_from_payload_site_reads_payload(hub_db_unscoped):
    org_a, _ = _two_orgs()
    with session_scope() as s:
        got = sync_svc.org_id_from_payload(
            s, "site", {"organization_id": org_a}
        )
        assert got == org_a


def test_org_id_from_payload_device_derives_via_site(hub_db_unscoped):
    """A Tier-B `device` payload's org is derived through its site."""
    org_a, _ = _two_orgs()
    with tenant_scope.org_context(org_a):
        with session_scope() as s:
            site = Site(name="hub-site")
            s.add(site)
            s.flush()
            site_id = site.id
    with session_scope() as s:
        got = sync_svc.org_id_from_payload(
            s, "device", {"site_id": site_id}
        )
        assert got == org_a


def test_org_id_from_payload_user_has_no_org(hub_db_unscoped):
    """A `user` is M:N to orgs — no single owning org."""
    with session_scope() as s:
        assert sync_svc.org_id_from_payload(
            s, "user", {"email": "u@example.com"}
        ) is None
