# B1 RBAC — first implementation slice (design)

| Field | Value |
|---|---|
| Date | 2026-05-15 |
| Status | **P1+P2+P3 SHIPPED** (v0.5.35/.36/.37, 2026-05-15). P4–P5 implementable per §4. |
| Owners | rebooter-droids backend/web |
| Live version | v0.5.34 at `https://www.voipguru.org/rebooter` |
| Backlog item | `docs/BACKLOG.md` B1 |
| Source of truth | `docs/RFC-003-web-ui-redesign.md` §9.0 (decisions locked 2026-05-10) |
| Related plan | `docs/redesign-continuation-plan-v2.md` Tier-A (A1..A9) |
| Successor blocker | B11 multi-hub sync (outbox events must carry scope claims) |

> **About this doc.** Concretises the next implementable slice of B1
> on top of what already shipped in v0.5.0/v0.5.1 (Tier-A / A1). It
> does **not** redesign the RBAC shape — RFC-003 §9.0 already locked
> that. The objective here is to translate that shape into a phased
> rollout that engineering can ticket against, with the highest-risk
> areas called out so each ship is small enough to remain reversible.

---

## 1. Current state (as of v0.5.34)

### 1.1 What is already shipped

| Piece | Where | Notes |
|---|---|---|
| `role_bindings` table | `app/models/role_bindings.py` | `(user_id, scope_type, scope_id?, role)` with `scope_type ∈ {global, site, group, device}`. Unique on `(user_id, scope_type, scope_id)`. Indexes on `user_id` and `(scope_type, scope_id)`. Created via `Base.metadata.create_all()` on container start. |
| One-shot backfill | `app/services/bootstrap.py::ensure_role_bindings_backfill` | super_admin → one `(global, NULL, super_admin)` row; admin → one row per current site (or `(global, NULL, admin)` safety net if no sites); operator/viewer → no rows. Idempotent via `runtime_settings[rbac.role_bindings_backfilled_at]`. v0.5.1 correction step deletes incorrectly-granted operator rows from the v0.5.0 ship and re-runs the backfill cleanly. |
| Service-layer CRUD | `app/services/role_bindings.py::grant / revoke / list_for_user` | Idempotent re-grant updates role + `updated_at`. Validates `scope_id` shape against `scope_type`. |
| Effective-scope resolvers | same file | `has_global_role`, `effective_site_ids`, `effective_device_ids`, `can_act_on_device`, `can_act_on_site`. Returns either the `"ALL"` sentinel for global bindings or a concrete `set[str]`. Computes device-set as the union of: global, site→devices via `Device.site_id`, group→devices via `GroupMembership.group_id`, direct device bindings. |
| Role hierarchy | same file (`_ROLE_RANK`) | `super_admin (4) > admin (3) > operator (2) > viewer (1)`. `_role_satisfies(have, need)` is the at-or-above predicate. |
| Test precedent | `tests/qa/test_v0500_role_bindings.py` | Smoke-level (version probe + back-compat). Will deepen alongside each phase below. |

### 1.2 What is missing

| Gap | Impact |
|---|---|
| **No shadow-mode middleware (A2).** | The role-binding resolver is dead weight — it returns the right answer but nothing calls it. We cannot start the 7-day clean-shadow soak gate that the cut-over plan (A8) requires. |
| **No admin UI for granting/revoking bindings (A6 + A7).** | The only way to populate non-default bindings today is a Python shell on the container. Operators cannot self-serve site/group/device delegation. |
| **No scope-aware list/detail filtering (A4).** | `/app/devices`, `/app/groups`, `/app/sites`, `/app/history` all return the global row-set regardless of the caller's bindings. After the enforce flip this becomes a confusing UX (the caller can see rows they can't act on). |
| **Invitations don't carry scope (A5 — B10/Q4).** | `invitations.role` is a single string. There is no `scope_type / scope_id` column, no multi-scope list, and no UI on the invite form. New users land as global-role by default. |
| **No audit-events archive + retention setting (A9 — B10/Q3).** | `system.audit_retention_days` runtime setting + nightly soft-prune + `audit_events_archive` table not yet on disk. Independent of enforcement; can ship in parallel. |
| **No `Device.site_id NOT NULL` enforcement (A3).** | `Device.site_id` is nullable; a device with `site_id=NULL` is invisible to site-scoped admins and falls through every site-binding filter. Backfill required before A8. |
| **Decorators are role-only, not scope-aware.** | `role_required_api(*ADMIN_AND_UP)` checks `user.role ∈ {...}`; it never consults `role_bindings`. The decorator surface needs an additional `scope_check=` parameter (or a paired helper) before any blueprint can be migrated. |
| **Outbox events don't exist yet (B11 dependency).** | Out of scope here, but every per-resource mutation we audit through the new scope-check pathway is the natural future home for the `outbox_events` write. Phase 1 needs to leave a stable seam for that without committing to the multi-hub work. |

### 1.3 What surprised the design pass

- **The backfill already exists and is idempotent.** The shape locked in B10 §9.0 is on disk. We are not designing schema; we are designing *enforcement*. This is much smaller than the BACKLOG entry suggested.
- **The resolver already supports the full Site+Group+Device cardinality.** No re-architecture needed — `effective_device_ids` already unions all four scope tiers. Phase 1 is "wire it up," not "build it."
- **v0.5.0 had an over-grant bug that v0.5.1 corrected.** Lesson: the backfill query must gate on `users.role`, not `users.is_admin` (which is True for operators too). Carrying the lesson forward — every future RBAC migration test must include "an operator with `is_admin=True` does not end up with admin bindings."
- **Group→device resolution is already O(membership-row) via `GroupMembership`.** For fleets under ~10k devices, this is cheap enough at request time without caching. Phase 1 does not need a materialised view.

---

## 2. Proposed schema changes

All changes are additive and follow the `_PENDING_COLUMNS` pattern in
`app/services/bootstrap.py` so no Alembic migration file is required —
container start runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`.

### 2.1 Invitations: scope columns (Phase 3)

```
invitations:
    scope_payload  JSONB    NULL   -- one or more (scope_type, scope_id) tuples, or NULL for legacy global
```

Rationale for JSONB over a join table:
- The invite is short-lived (30-day TTL) and consumed once; relational
  integrity adds cost without benefit.
- B10/Q4 explicitly allows multi-scope at send-time (site-multi-select +
  group/device selector). A JSONB list keeps the shape flexible.
- On redemption, the consumer is the *only* writer that turns the payload
  into N concrete `role_bindings` rows. Foreign-key validity is checked
  at that moment, not at invite-issue time (sites/groups can be deleted
  between send and redeem; we want the redemption to *fail loudly* in
  that case, not silently auto-prune).

`scope_payload` shape (locked):

```jsonc
{
  "bindings": [
    { "scope_type": "site",   "scope_id": "site_01H..." },
    { "scope_type": "group",  "scope_id": "grp_01H..."  },
    { "scope_type": "device", "scope_id": "dev_01H..."  }
  ]
}
```

`NULL` → legacy behaviour: redeem activates the user with `users.role`
only, no rows in `role_bindings`. This preserves back-compat for any
invites already in flight when Phase 3 deploys.

### 2.2 Audit-events archive (Phase 5, can land in parallel)

```
audit_events_archive  (mirror of audit_events shape, plus archived_at TIMESTAMPTZ)
runtime_settings row:  system.audit_retention_days  default 365
```

Nightly job (APScheduler tick, hourly with a date-rollover guard): copy
rows older than `system.audit_retention_days` into `audit_events_archive`,
delete from the source table.

### 2.3 No changes to `role_bindings`, `devices`, `groups`, `sites`

The model on disk is already correct. Phase 2 of this plan **does**
enforce `Device.site_id NOT NULL` but the column itself already exists;
we are flipping nullability with a backfill, not adding a column.

---

## 3. Enforcement strategy

### 3.1 Decision: paired primitives at the decorator + request-helper layer

Two surfaces, one signature pattern:

```python
# In app/middleware/admin_auth.py — alongside role_required_*

def scope_required_api(role_needed: str, *, scope_resolver):
    """Pair a role-required check with a resource-specific scope
    check. `scope_resolver(user, view_kwargs) -> bool` returns
    whether the caller may act on the resource identified by the
    route. False ⇒ 403 (shadow: log + audit; enforce: deny)."""

# Mirror UI variant
def scope_required_ui(role_needed: str, *, scope_resolver): ...
```

```python
# In app/services/role_bindings.py — request-scoped helpers

def require_can_act_on_device(device_id: str, role_needed: str) -> None:
    """Raise PermissionDenied / log shadow row depending on enforce-flag."""

def require_can_act_on_site(site_id: str, role_needed: str) -> None: ...
def require_can_act_on_group(group_id: str, role_needed: str) -> None: ...
```

**Why both layers.** Decorators cover the predictable case ("the route
URL contains the resource id"). The request-scoped helpers cover the
N-of-M case (bulk endpoints, queries with a filter, mass-action
confirmations) where the per-resource check is buried inside the
handler. Existing code already has examples of both patterns; we are
not inventing a new style.

**Why not just put it in `role_required_api`.** Conflating role and
scope into a single decorator was attempted in the redesign-plan-v1
brief and abandoned because every blueprint already calls
`role_required_api(*ADMIN_AND_UP)` and that means "any admin can hit
this endpoint" — which is the *role* truth even after RBAC enforcement
lands. The scope check is a separate concern at a separate frame in
the call stack.

### 3.2 Shadow mode is a runtime flag, not a code branch

```python
# runtime_settings row, env-var fallback
rbac.enforce_mode  ∈  {"shadow", "enforce"}    # default: "shadow"
```

When `shadow`:
- The scope check still runs.
- A `False` return writes an audit row with `action='rbac.shadow_deny'`
  and `details={route, method, scope_type, scope_id, reason, role_needed}`.
- The request proceeds — legacy `role_required_*` continues to be
  authoritative.

When `enforce`:
- A `False` return raises / 403s the request.
- We still emit `action='rbac.enforce_deny'` on every block so the
  audit trail mirrors shadow-mode for post-incident analysis.

The runtime flag is the **only** difference. Every blueprint we migrate
to scope checks works correctly in both modes; the cut-over (A8) is one
runtime-setting toggle, not a code deploy.

### 3.3 Audit trail

`rbac.shadow_deny` / `rbac.enforce_deny` rows are first-class audit
events using the existing `app.services.audit.record(...)` API. No new
table needed. The shadow soak ends when:

- Zero `rbac.shadow_deny` rows for ≥ 7 calendar days on production
  traffic, OR
- All `rbac.shadow_deny` rows have been classified by the operator as
  "expected — caller should not have been able to do this" (i.e., the
  shadow is catching real over-permission and we want enforce).

The Settings → System tab gains a small panel showing
`shadow_deny count (7d)` so the operator can watch the gate.

### 3.4 The `super_admin` escape hatch

Every check short-circuits on `has_global_role(user_id, ROLE_SUPER_ADMIN)`.
A super_admin is never denied. This matches B10/Q1 and §9.0 explicitly.

### 3.5 Outbox-events seam (B11 forward-compat)

Each successful per-resource mutation routes through a single
choke-point helper that, **today**, only writes the audit row. When B11
ships, that same helper grows a second writer that appends to
`outbox_events` with the scope claim already attached. By concentrating
all per-resource writes through one helper now, Phase 1 unblocks B11
without committing to it.

```python
# app/services/audit.py — additive

def record_scoped(
    action: str, *,
    actor_user_id: str | None,
    target_type: str, target_id: str,
    scope_claim: dict,                # {scope_type, scope_id}
    details: dict | None = None,
) -> None:
    """Wraps record() and (later) outbox_events.append()."""
```

Callers stop calling `record()` directly for any per-resource mutation
and call `record_scoped()` instead. Two-line change per call site.

---

## 4. Phased rollout (5 ships, each independently deployable)

The original A1..A9 plan in `redesign-continuation-plan-v2.md` is
**re-grouped** here so each ship is small enough that a failure rolls
back cleanly. Mapping to the A-tier IDs is in the right column.

| Ship | Version | Lands | A-tier mapping | Reversible? |
|---|---|---|---|---|
| **P1 — Decorator + helper foundation, shadow mode wired but inert** — ✅ **SHIPPED v0.5.35** (2026-05-15) | v0.5.35 | `scope_required_api` / `scope_required_ui` / `require_can_act_on_*` primitives; `rbac.enforce_mode` runtime setting (default `shadow`); `rbac.shadow_deny` + `rbac.enforce_deny` audit actions; `record_scoped()` choke-point; **two demonstrator routes wired** — `GET /api/v1/admin/devices/<id>` + `POST /api/v1/admin/devices/<id>/commands` (read + write proof). Regression test `tests/qa/test_v0535_rbac_shadow_skeleton.py`. Everything else unchanged. | A2 (partial) | Yes — only two routes use the new pathway; revert is a one-PR back-out. |
| **P2 — Device.site_id NOT NULL + audit-archive table** — ✅ **SHIPPED v0.5.36** (2026-05-15) | v0.5.36 | Backfill any `Device.site_id IS NULL` to a freshly-created `Default` site (or reuse the operator's first existing site if exactly one exists); flip the column to `NOT NULL` via `_PENDING_CONSTRAINTS`-style `ALTER`. Independently: ship `audit_events_archive` table + `system.audit_retention_days` runtime setting (default 90 days) + nightly soft-prune APScheduler tick at 03:00 UTC. Regression test `tests/qa/test_v0536_site_not_null_and_archive.py`. | A3 + A9 | Mostly — the NOT NULL flip is one-way without a manual ALTER, but the prune job is feature-flagged and the archive table is additive. |
| **P3 — Scope-aware list/detail filtering on the four big surfaces** — ✅ **SHIPPED v0.5.37** (2026-05-15) | v0.5.37 | `/app/devices` + `/api/v1/admin/devices` list endpoints filter by `effective_device_ids`. Same for `/app/groups`, `/app/sites`, `/app/history`. Still shadow — filtering happens *only* if `rbac.enforce_mode == enforce`; in shadow we **double-query** (full set and scoped set) and log the diff as `rbac.shadow_diff` rows. New `app/services/rbac_filter.py` with four filter functions; integrated into list services. Regression test `tests/qa/test_v0537_scope_filter_lists.py`. | A4 | Yes — pure read-path code; revert is a flag-toggle. |
| **P4 — Invitations carry scope + admin UI for granting/revoking** | v0.5.38 | `invitations.scope_payload` JSONB column; invite form gains site-multi-select + group/device pickers; redemption flow writes the N concrete `role_bindings` rows. Plus a per-user binding editor at `/app/settings/users/<id>` (super_admin only initially) showing current bindings + add/remove buttons. Per-site members tab on `/app/sites/<id>`. | A5 + A6 + A7 | Mostly — schema column is additive; UI gating uses the existing role decorator so a buggy editor cannot escalate. |
| **P5 — Migrate every blueprint to the scope primitives + flip enforce** | v0.5.39 → v0.5.40 | Two ships. First ship (`v0.5.39`): every per-resource route on every admin blueprint adopts `scope_required_*` or `require_can_act_on_*`. Still shadow. Soak for ≥ 7 days. Second ship (`v0.5.40`): toggle `rbac.enforce_mode = enforce` via the System tab; ship a rollback "shadow" toggle alongside so an unexpected production deny can be reverted without redeploy. | A2 (rest) + A8 | The enforce flip is a one-setting toggle; rollback is the inverse toggle, no code change. The blueprint sweep is a large diff but each route is a 1–3 line addition. |

### 4.1 Gating

- P1 → P2: independent; can ship same week.
- P2 → P3: P3's filter relies on `Device.site_id` being non-null. Block.
- P3 → P4: P4's UI assumes the read filter from P3 exists. Soft block;
  P4 will work without P3 but the operator UX is degraded.
- P4 → P5: hard block. P5 only makes sense once operators can actually
  grant/revoke bindings without a Python shell.
- P5 internal: first ship must soak ≥ 7 calendar days with **zero**
  `rbac.shadow_deny` rows before the enforce toggle.

### 4.2 The B11 multi-hub sync unblock

B11 (`docs/RFC-004 §10b` Option C) explicitly waits for "scope claims on
outbox events." That requirement is satisfied at the end of P3 — every
per-resource mutation now routes through `record_scoped()` which carries
the `(scope_type, scope_id)` tuple. B11 can begin design in parallel
with P4 and ship after P5.

---

## 5. Test approach

`tests/qa/test_v0500_role_bindings.py` is the precedent. Each phase adds
a sibling file:

| Phase | New test file | Asserts |
|---|---|---|
| P1 | `tests/qa/test_v0535_rbac_shadow_skeleton.py` | (a) `rbac.enforce_mode` runtime setting defaults to `shadow`; (b) hitting `/api/v1/admin/devices/<id>` as a user with NO bindings logs a `rbac.shadow_deny` audit row but still 200s; (c) super_admin never produces a shadow_deny row; (d) flipping the setting to `enforce` makes the same request 403 instead of 200; (e) `record_scoped()` always emits a normal audit row in addition to any deny row. |
| P2 | `tests/qa/test_v0536_site_not_null_and_archive.py` | (a) every existing `devices` row has non-null `site_id` after the backfill; (b) inserting a `devices` row with `site_id=NULL` raises; (c) `audit_events_archive` exists with the right shape; (d) the nightly prune job, run manually with `system.audit_retention_days=1` against a seeded old row, moves the row and removes the source. |
| P3 | `tests/qa/test_v0537_scope_filter_lists.py` | A multi-user fixture: super_admin sees all; an admin-of-site-A sees only site-A devices/groups/history; an operator with one device binding sees only that device; viewer with site binding sees site rows but cannot mutate. Shadow mode emits `rbac.shadow_diff` rows whose count matches the row-count delta. |
| P4 | `tests/qa/test_v0538_invitation_scope_redemption.py` | (a) invite send with `scope_payload={bindings: [...]}` stores the JSONB; (b) invite redemption creates the N expected `role_bindings` rows for the new user; (c) invite with a stale `scope_id` (deleted site between send and redeem) fails loudly with a clear error; (d) per-user binding editor add/remove flows audit correctly; (e) a non-super_admin cannot grant a `super_admin` global binding. |
| P5 | `tests/qa/test_v0539_full_blueprint_sweep.py` + `test_v0540_enforce_flip.py` | The sweep test scripts a non-super-admin user through every admin route and asserts: in shadow mode no 403s; in enforce mode the expected route returns 403 for resources outside scope, 200 for resources inside scope. The enforce-flip test toggles the setting via the API and asserts behaviour switches without a container restart. |

All five test files run under the existing pytest harness against the
local container; no new tooling.

---

## 6. Highest-risk areas

| Risk | Mitigation |
|---|---|
| **Touches every blueprint.** A sweep across `app/blueprints/admin/*.py` is a wide diff. | Land P5's blueprint sweep as a single PR with the `rbac.enforce_mode = shadow` so the diff is observable in production for ≥ 7 days before it can deny anyone. |
| **Backfill semantics on `Device.site_id` NOT NULL.** A device with `site_id=NULL` either belongs to "the default site" or "no site, hide from everyone." Picking wrong locks the operator out. | Default to assigning to a *single existing* site if exactly one exists in the database; otherwise create a literal `"Default"` site (matches RFC-003 §9.2). Open Q1 below. |
| **Group bindings can be a surprise vector.** A binding to a group grants action on every device currently in that group — but `GroupMembership` changes after the binding does. | Document explicitly: group bindings are *live*, not snapshot. Adding a device to a group widens the binding's reach. The per-user binding editor must show this in plain English. |
| **The shadow soak gate.** Operators may grow tired of shadow mode and flip enforce before the soak completes. | Block the System-tab toggle behind a "I have reviewed N days of zero shadow_deny rows" confirmation. Recommend the toggle have a 24h "preview enforce mode" sub-mode where it auto-reverts. (Defer the preview mode if it adds scope to P5.) |
| **Admin tool to grant/revoke** lives at `/app/settings/users/<id>`. A bug here is privilege-escalation-shaped. | Restrict the editor to super_admin only in P4. Site-admin-can-edit-their-site-members is a P6 stretch goal. Audit *every* grant/revoke. Forbid creating `(global, super_admin)` bindings via the UI — that path stays Python-shell-only forever. |
| **Outbox-events forward-compat.** If we don't add the `record_scoped()` choke-point in P1, every B11 ship has to retro-wire it later. | P1 adds the choke-point even though it only wraps `record()` today; the second writer lands in B11's first ship. |
| **`Device.site_id` filter performance.** `effective_device_ids` does `Device.site_id.in_(site_ids)` for each request. | Index already exists (`ix_devices_site_id`). For fleets > 10k devices revisit by caching the resolver result on `flask.g` for the lifetime of the request. |
| **Roll-out coordinated with B16 power-analytics.** B16 introduces new write paths that must also route through `record_scoped()`. | B16's design doc must reference this one and adopt the choke-point from day one. Add a note to `docs/B16-power-analytics-design.md` after P1 lands. |

---

## 7. Estimated effort

| Phase | Engineering hours | Notes |
|---|---|---|
| P1 | 4–5 h | Decorator + helpers + runtime flag + two demonstrator routes + audit action + P1 test file. |
| P2 | 3–4 h | Site backfill + NOT NULL flip + archive table + prune job + P2 test file. Independent ships possible if a particular flake shows up on either piece. |
| P3 | 4–5 h | Four list endpoints + four detail endpoints + double-query shadow-diff logging + P3 test file. |
| P4 | 6–8 h | `invitations.scope_payload` + invite form widgets + redemption logic + binding editor UI + sites members tab + P4 test file. Most UI work in the whole plan. |
| P5 | 5–7 h sweep + 1 h flip | Cross-blueprint sweep (~25 routes × 1–3 line addition each, but mechanical). Flip ship is small but must come after a 7-day soak. |
| **Total** | **~22–30 h** | Matches the redesign-plan-v2 estimate of "~23 h for Tier-A." |

Plan is consistent with the v2 sprint table's allocation of v0.5.x =
RBAC; the version line stays in the 0.5 series. If operator prefers,
P5's enforce flip becomes the v0.6.0 cut.

---

## 8. Open questions (only the ones that block coding)

1. **`Device.site_id` backfill default — single-site reuse vs always-create-Default.** If the DB has exactly one site, do we backfill `site_id=NULL` rows to that one, or always create a `"Default"` site and use it? *Recommendation: reuse if there is exactly one; create-Default otherwise. Matches the principle-of-least-surprise — a single-site operator does not get an empty extra row in their site picker.*

2. **Per-user binding editor scope (P4) — super_admin only on first ship, or also site-admin?** Site-admins managing their own site's members is a real product win, but it widens P4's blast radius. *Recommendation: super_admin only in P4; add site-admin-can-edit-their-site in a small P4.5 ship after observation.*

3. **`rbac.enforce_mode` preview sub-mode.** Should the System-tab toggle have a "preview enforce for 24h, auto-revert" option to de-risk the cut-over, or is the simple two-state toggle good enough? *Recommendation: two-state in P5; add preview later if the cut-over surfaces real surprise denials.*

Every other question raised in the v2 plan §"Open questions" is
*answerable from existing code or RFC-003 §9.0 itself*:

- **Default site name** → use literally `"Default"`. (RFC-003 §9.2)
- **Group/device binding implication** → *additive only*, no implication. (Matches how `effective_device_ids` already unions sources.)
- **`audit_events_archive` rotation** → archive grows unbounded by default; add a `system.archive_retention_days` setting later if a real compliance-driven request surfaces. (Out of scope for this slice.)
- **Shadow-mode log location** → `audit_events` with `action='rbac.shadow_deny'` (and `rbac.shadow_diff` for read-path divergence in P3). No new table.
- **Cut-over rollback plan** → the `rbac.enforce_mode` runtime setting *is* the rollback. Two-line revert with no deploy.

---

## 9. What this slice deliberately does NOT do

- **No new role names.** `super_admin / admin / operator / viewer` stay. RFC-003 §14 promises this.
- **No new scope types.** `global / site / group / device` is what B10/Q1 locked. Anything finer (per-tag, per-blueprint) is a future RFC.
- **No outbox-events implementation.** The seam is added (`record_scoped()`) but the receiver-side replicator is B11's job.
- **No SSO / OIDC / TOTP integration.** Those are Tier-B (B1..B5 of v2). They depend on this work but ship after.
- **No per-route role overrides in the database.** Decorators stay in code. A DB-driven policy engine is over-engineering for a < 10-tenant product.

---

## 10. Appendix — exact code seams to touch (P1)

For the engineering pickup:

- `app/middleware/admin_auth.py` — add `scope_required_api`, `scope_required_ui`. Import from `app.services.role_bindings`. Keep `role_required_*` untouched.
- `app/services/role_bindings.py` — add `require_can_act_on_device / _site / _group` that read `rbac.enforce_mode` and either raise a `PermissionError`-shaped error or emit an audit row.
- `app/services/audit.py` — add `record_scoped(...)` that wraps `record()` and (later) `outbox_events.append()`. Two-line change.
- `app/services/runtime_settings.py` — no API change; only a documented new key `rbac.enforce_mode` with default `"shadow"`.
- `app/blueprints/admin/devices_api.py` — wire two routes (`GET /<id>` + `POST /<id>/commands`) through `scope_required_api(ROLE_OPERATOR, scope_resolver=...)`. Single-blueprint demonstrator.
- `tests/qa/test_v0535_rbac_shadow_skeleton.py` — new file matching the precedent of `test_v0500_role_bindings.py`.

Every other phase touches *more* but follows the same module-boundary
discipline already enshrined in `docs/architecture.md`.
