# Hard multi-tenant `organization` boundary — design & migration plan

> **Status:** DESIGN PASS — 2026-05-20. Author: D. Blagbrough.
> Scope: introduce a first-class top-level `organization` (tenant) entity
> into the Rebooter Hub (Python/Flask/Postgres/SQLAlchemy, ~v0.5.102) and
> the enforced isolation model around it. READ-ONLY design pass — no source
> modified, nothing committed. This document proposes; it does not commit
> to a release number.
> Companion doc: `docs/notes/2026-05-20-hub-tier2-design.md` — its open
> question Q1 ("is a hard tenant boundary imminent?") is now answered YES
> by this owner decision; §7 below tells the Tier-2 work what to do.

## 0. Why this exists / current state

The hub is a public, internet-facing **paid multi-tenant SaaS** (also
self-hostable). Today there is **no tenant boundary**. The only seams are:

- `Device.site_id` → `sites.id` (`app/models/devices.py:52`), nullable,
  `ON DELETE SET NULL`. A startup backfill is mid-flight to make it
  `NOT NULL` (`app/services/bootstrap.py:143-151`, `:320-396`).
- `WatchdogRule.site_id`, `EnrollmentToken.site_id` (`app/models/devices.py:180`),
  `Group.site_id` (`app/models/groups.py:21`) — all nullable FKs to `sites`.
- RBAC `role_bindings` (`app/models/role_bindings.py`) with
  `scope_type ∈ {global, site, group, device}` and an effective-scope
  resolver (`app/services/role_bindings.py:198-251`).

There is **no row that says "this data belongs to paying customer X."**
`sites` has no owner (`app/models/sites.py` — just `id`, `name`,
`description`, timestamps). `users.email` is **globally unique**
(`app/models/users.py:25`) and a user has no tenant — a user is implicitly
"a user of the whole hub." `Site.name`, `Group.name`, `Scene.name`,
`User.email` all carry **global `unique=True`** constraints — these are the
first concrete things that break under multi-tenancy and the first things
this design has to fix.

Isolation today is *entirely* "remember to filter by `site_id` and run
list queries through `rbac_filter.py`." That is exactly the "remember to
filter every query" model the owner has decided to replace. RBAC enforce
mode is still **shadow** (`app/services/role_bindings.py:302`,
`enforce_mode()` default `ENFORCE_MODE_SHADOW`), so even the partial
isolation that exists is not enforced in production yet.

---

## 1. The `organization` model

A new table `organizations`, a new model file `app/models/organizations.py`,
registered in `app/models/__init__.py` near the top (before `User` and
`Site`, since both will FK to it).

```python
# app/models/organizations.py  (proposed)
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "org"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    # Lifecycle / billing — kept deliberately small for v1.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active")     # active|suspended|closed
    plan: Mapped[str] = mapped_column(
        String(40), nullable=False, default="free")       # free|pro|enterprise...
    is_self_hosted_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)           # the lone org on a
                                                          # self-host install
    # Soft caps for the SaaS plan tiers (NULL = unlimited).
    max_devices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Owner — the human who created/owns the org. SET NULL so deleting a
    # user never orphans the org; org survives, owner re-assigned by admin.
    owner_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()
```

Key choices and the evidence behind them:

- **ULID PK with `org_` prefix** — matches every other model
  (`new_id("site")` etc., `app/models/_helpers.py:25-27`). Sync,
  tombstones and `scope_id` columns are all `String(40)` and prefix-typed,
  so `org_…` slots in with no width change.
- **`slug` unique, `name` not unique** — `name` becomes a per-org display
  string; `slug` is the stable, URL-safe, globally-unique handle (used in
  signup, in a future `/o/<slug>/…` URL scheme, in support tooling). This
  is the *one* table that legitimately keeps a global unique constraint.
- **`owner_user_id` is `SET NULL`, not `CASCADE`** — consistent with the
  existing pattern (`FirmwareRelease.created_by_user_id`,
  `audit_events.actor_user_id` are all `SET NULL`,
  `app/models/firmware.py:39`, `app/models/audit.py:33`). An org must
  outlive any individual user; ownership is transferable.
- **`status`/`plan`/`max_*`** — minimum viable billing surface. Suspending
  an org (non-payment) is a *single column flip*, which §3 turns into a
  hub-wide lockout. The caps are advisory in v1 (checked at create-time in
  the service layer); enforce later.

### Relationships / ownership graph

```
organizations (1) ──< sites (N)            sites.organization_id  (NOT NULL, RESTRICT)
sites         (1) ──< devices (N)          devices.site_id        (already exists)
organizations (1) ──< org_memberships (N) >── users   (M:N join, see §4)
organizations (1) ──< groups, watchdog_rules, schedules, scenes, … (N)
```

The decision that shapes everything: **`organization` owns `site`; `site`
owns `device`.** A device's org is *derived* through its site
(`device → site → organization`) and is **not** stored on the device row.
Reasoning:

- It removes a whole class of bug — a device whose `site_id` and
  `organization_id` disagree (split-brain ownership). With a derived org
  there is exactly one source of truth.
- Moving a device between sites is already supported; if org were a column
  on `devices` it would have to be kept in sync on every site change.
- The cost — joins through `sites` to filter devices by org — is paid once,
  centrally, by the scoped-session mechanism in §3, not scattered.

The same "derive, don't denormalize" rule applies to every **child** of a
device or site (heartbeats, power samples, events, commands, credentials):
they get **no** `organization_id`. Their org is `child → device → site →
organization`. Only **org-direct** entities (those a tenant administers
directly) get an `organization_id` column — see §2.

---

## 2. Which tables get `organization_id`

Two tiers. **Tier-A — direct `organization_id` column.** **Tier-B —
no column, org derived via a parent.** Getting this split right is the
core of keeping the migration small and the isolation cheap.

### Tier-A — gets `organization_id` (NOT NULL, FK `organizations.id`)

| Table | File:line evidence | FK on-delete | Notes |
|---|---|---|---|
| `sites` | `app/models/sites.py:13` | `RESTRICT` | The pivot. Org owns sites. |
| `users` | `app/models/users.py:19` | — | *Special — see §4.* Users are M:N to orgs via a join table; `users` itself does **not** get the column. Listed here only to flag it as a decision point. |
| `groups` | `app/models/groups.py:13` | `RESTRICT` | Has `site_id` today but it is nullable; a group with `site_id=NULL` still needs an owning org. Add `organization_id` directly. |
| `watchdog_rules` | `app/models/watchdog.py` | `RESTRICT` | Has nullable `site_id`; same reasoning as groups. |
| `schedules` | `app/models/schedules.py` | `RESTRICT` | Org-direct config. |
| `scenes` | `app/models/scenes.py:26` | `RESTRICT` | Currently has **no** site link at all — pure global table today. Org boundary closes that gap. |
| `enrollment_tokens` | `app/models/devices.py:170` | `RESTRICT` | A token mints a device into an org's site. |
| `device_announcements` | `app/models/announcements.py` | `SET NULL` | Pre-adoption; may have no org yet. Nullable until adopted. |
| `external_sensor_sources` | `app/models/external_sensors.py` | `RESTRICT` | Operator-registered integrations. |
| `firmware_releases` / `firmware_deployments` | `app/models/firmware.py:25` | **see note** | *Decision needed — Q3.* Firmware is plausibly platform-global (one firmware catalog all tenants pull from) rather than per-tenant. Recommend: keep firmware **platform-global, no `organization_id`** for the SaaS; deployments are scoped because they target devices/sites/groups which are scoped. |
| `runtime_settings` | `app/models/runtime_settings.py:27` | **see note** | *Decision needed — Q4.* Today a flat global KV (`smtp.*`, `network.*`, `rbac.*`). Some keys are genuinely platform-global (network, RBAC enforce mode); some are per-org (branding, SMTP-from, notification prefs). Recommend: leave `runtime_settings` global for platform keys, and add a **separate** `organization_settings` table (`organization_id`, `name`, `value` JSON) for tenant-editable keys. Do **not** retrofit a nullable `organization_id` onto the existing global KV — it muddies the platform/tenant line. |
| `api_tokens`, `notification_channels`, `notification_subscriptions` | Tier-2 design (not yet built) | `RESTRICT` / `CASCADE` | See §7. |
| `signup_requests` | `app/models/signup_requests.py` | n/a | Special — these *precede* org creation; see §5. No FK; carries an optional `created_organization_id` after approval. |
| `invitations` | `app/models/invitations.py` | `CASCADE` | An invite is into a specific org. See §4/§5. |

### Tier-B — NO column, org derived via parent

| Table | Derivation path | Evidence |
|---|---|---|
| `devices` | `device → site` | `app/models/devices.py:52` |
| `device_credentials` | `→ device → site` | `app/models/devices.py:151-167` |
| `device_heartbeats` | `→ device → site` | `app/models/devices.py:209` |
| `device_events` | `→ device → site` | `app/models/events.py:11` |
| `device_power_samples` / `device_power_rollups` | `→ device → site` | `app/models/power_analytics.py:21` |
| `commands` / `command_results` | `→ device → site` | `app/models/commands.py:29` |
| `group_memberships` | `→ group` (and `→ device`) | `app/models/groups.py:30` |
| `watchdog_probe_events` | `→ watchdog_rule` | `app/models/watchdog.py` |
| `external_sensor_samples` | `→ external_sensor_source` | `app/models/external_sensors.py` |
| `attention_acks`, `device_failsafe_events` | `→ device` | — |
| `role_bindings` | `→ user` + scoped resource | `app/models/role_bindings.py:55` — see §4 |
| `audit_events` / `audit_events_archive` | mixed; **see §3.6** | `app/models/audit.py:20` |
| `sessions`, `password_resets` | `→ user` | — |
| `outbox_events`, `sync_cursors`, `tombstones` | sync infra; **see §3.7** | `app/models/sync.py` |

**Important nuance — high-volume Tier-B tables.** `device_heartbeats`,
`device_power_samples`, `device_events`, `audit_events` are the
write-hot, billions-of-rows tables. Deriving their org through a 2–3 hop
join on *every* query is the one real performance risk of "derive, don't
denormalize." Two mitigations, pick per-table at build time:

1. **Index the join path well** — `devices.site_id` is already indexed
   (`app/models/devices.py:147`); add `sites.organization_id` index. A
   query filtered to one org's devices then narrows fast.
2. **Denormalize `organization_id` onto the hottest 1–2 tables only**
   (most likely `device_heartbeats` and `device_power_samples`) as a
   *read-optimization*, written by the ingestion path from the device's
   site, never user-editable. This is a deliberate, localized exception
   to the derive rule — make it consciously, not by default. Recommend:
   start without it; add it only if query plans demand it post-rollout.

---

## 3. Tenant isolation — the security-critical part

**Requirement:** make cross-org data access *structurally* hard, so that a
forgotten `.where(organization_id == ...)` is a *closed* hole, not an open
one. The current `rbac_filter.py` approach is opt-in per call site
(`filter_devices_with_shadow_logging` etc., `app/services/rbac_filter.py:76`)
— a developer who writes a new query and forgets to route it through the
filter silently leaks. That is the failure mode to design out.

### 3.1 Recommended mechanism: a tenant-scoped Session with a global FROM-clause filter

Adopt SQLAlchemy's **`with_loader_criteria` applied automatically via an
`orm.Session` `do_orm_execute` event** — a "global filter" pattern. This
is the SQLAlchemy-blessed way to make every ORM `SELECT` against a
tenant-owned entity carry a `WHERE organization_id = :current_org`
**without the call site doing anything**.

Concretely, a new module `app/services/tenant_scope.py`:

```python
# app/services/tenant_scope.py  (proposed sketch)
from contextvars import ContextVar
from sqlalchemy import event, orm
from sqlalchemy.orm import with_loader_criteria

# The active tenant for the current request/job. ContextVar, not flask.g,
# so background jobs and the scheduler can set it too.
_current_org: ContextVar[str | None] = ContextVar("current_org", default=None)
_bypass: ContextVar[bool] = ContextVar("tenant_bypass", default=False)

class TenantScoped:
    """Mixin marker. Any model that mixes this in is auto-filtered."""

@event.listens_for(orm.Session, "do_orm_execute")
def _add_tenant_filter(execute_state):
    if _bypass.get():
        return
    org_id = _current_org.get()
    if org_id is None:
        return  # unscoped context — see 3.4
    if not execute_state.is_select:
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.organization_id == org_id,
            include_aliases=True,
        )
    )
```

Why this mechanism over the alternatives:

- **vs. a base `query` / repository layer:** the codebase uses
  `session.scalars(select(...))` directly *everywhere* (`auth.py:28`,
  `role_bindings.py:96`, `rbac_filter.py`, every service). There is no
  single query factory to subclass. A `do_orm_execute` event needs **zero
  call-site changes** for every Tier-A entity — it catches queries that
  do not exist yet, written by developers who never read this doc. That
  is the structural property the owner asked for.
- **vs. Postgres Row-Level Security (RLS):** RLS is the gold standard and
  worth considering, but it requires every connection to `SET app.org_id`
  per request, plays awkwardly with the connection pool
  (`pool_pre_ping`, `app/db.py:18`), and the `pg_advisory_lock` /
  bootstrap code runs as a superuser-ish role that bypasses RLS. RLS is
  the recommended **phase-2 hardening / defense-in-depth** layer (see
  §8), not the phase-1 mechanism. The application-level event filter
  ships first; RLS can be added underneath later without app changes.
- **vs. "just remember to filter":** explicitly rejected by the owner.

`TenantScoped` is a mixin carrying the `organization_id` column itself, so
"is this entity tenant-scoped?" is a single `isinstance`/MRO fact, and
`with_loader_criteria(TenantScoped, …)` filters *all* of them at once.
Tier-A models mix it in; Tier-B models do not (they are reached only via a
join from a Tier-A row, which is already filtered, or via `device_id` /
`site_id` predicates the service supplies).

### 3.2 Setting the scope — one `before_request` hook

A single `before_request` on the admin blueprints resolves the org from
the authenticated user and sets the `ContextVar`:

```python
@admin_ui_bp.before_request
@admin_api_bp.before_request
def _bind_tenant():
    user = g.get("current_user")          # set by role_required_* decorators
    if user is not None:
        tenant_scope.set_org(resolve_active_org(user))
```

The role decorators (`role_required_api`/`role_required_ui`,
`app/middleware/admin_auth.py:109-154`) already set `g.current_user`
before the view runs — but `before_request` runs *before* the decorator.
So the cleaner wiring is to set the org **inside** `role_required_*`
right after `g.current_user = user` is assigned
(`admin_auth.py:125` and `:149`) — two lines, one place, every
authenticated route covered. `tenant_scope.reset()` in an
`after_request`/`teardown` so a pooled worker never leaks the previous
request's org.

For a user who belongs to multiple orgs (§4), `resolve_active_org` reads
an "active org" from the session (`session["active_org_id"]`), defaulting
to their sole/primary membership; an org-switcher UI updates it. The
active org must always be validated against the user's memberships on
every request — never trust the session value blind.

### 3.3 Writes / INSERTs

`do_orm_execute` filters `SELECT`/`UPDATE`/`DELETE`. **INSERTs** need the
`organization_id` *set*, not filtered. Use a `before_flush` (or
`before_insert` mapper) event that stamps `organization_id` from the
ContextVar onto any `TenantScoped` instance that does not already have one,
and — critically — **raises** if a `TenantScoped` row is being inserted or
modified with an `organization_id` that differs from the active org. That
turns "service code forgot to set the org on a new Site" into a
guaranteed-correct default, and "attacker passes a foreign `organization_id`
in a form" into a hard `500`/rejected transaction rather than a
cross-tenant write.

### 3.4 The dangerous case: unscoped / system contexts

`do_orm_execute` does nothing when `_current_org` is `None`. That is
correct for genuine system contexts but is also the **single biggest
isolation-bug surface**, so it must be deliberate, not accidental:

- **Device API** (`/api/v1/device/*`, `app/blueprints/device_api.py`):
  authenticated by `device_credentials`, not a user. The device-auth
  middleware (`app/middleware/device_auth.py`) must resolve the device →
  its site → its org and set the ContextVar, exactly like the user path.
  A heartbeat write must land in the device's org. **This is mandatory,
  not optional** — without it the entire device-facing API is unscoped.
- **Background jobs / scheduler** (`app/jobs/scheduler.py`,
  `watchdog_runtime/`, `schedule_runtime.py`, `audit_prune.py`,
  `sync_replicator.py`): these legitimately operate across all orgs. They
  must run with an **explicit, audited bypass** — `with tenant_scope.system():`
  — never by simply leaving the ContextVar unset. The job iterates orgs/
  sites itself and is responsible for not crossing streams.
- **Bootstrap** (`app/services/bootstrap.py`): runs at startup before any
  request; explicit system bypass.
- **`session_scope()`** (`app/db.py:30`): unchanged. The event is on the
  `Session` *class*, so every `session_scope()` session gets the filter
  for free.

The rule: **`_current_org is None` must mean "explicit system bypass was
entered," never "we forgot."** Enforce by making the unscoped path emit a
loud warning audit row in non-system contexts during the shadow phase
(§8) — any `do_orm_execute` on a `TenantScoped` entity with `org is None`
and `_bypass is False` is a latent bug.

### 3.5 Auditing that every endpoint honors it

The mechanism is global, so the audit is "prove the mechanism is global,"
not "check 200 endpoints." Concretely:

1. **Static test:** a unit test that imports every model, asserts every
   Tier-A table from §2 mixes in `TenantScoped`, and fails CI if a new
   model with an `organization_id` column is added without the mixin (or
   a new Tier-A-looking table is added without `organization_id`). This
   is the "you can't forget" backstop.
2. **Cross-tenant integration test:** seed two orgs A and B with sites,
   devices, rules, groups, scenes, users. For **every** `/app/*` and
   `/api/v1/admin/*` list and detail route, authenticate as an A-user and
   assert no B-row id ever appears in the response, and that hitting a
   B-resource id directly returns 404 (not 403 — 404 avoids confirming
   existence). Drive it off the URL map so newly-added routes are covered
   automatically.
3. **Shadow phase** (§8): like RBAC's existing `rbac.enforce_mode`
   shadow/enforce pattern (`role_bindings.py:302-346`), run the tenant
   filter in a **count-and-log** mode first — execute the query both
   with and without the filter, and emit a `tenant.shadow_diff` audit row
   if they differ. Any diff in shadow = a place that was already leaking
   or a query that needs the bypass. ≥7 days clean before enforce flip,
   mirroring the RBAC playbook the team already trusts.
4. **The sync applier** (`sync_replicator.py`) gets its own targeted
   test — see §3.7.

### 3.6 `audit_events`

`audit_events` has `target_type`/`target_id` (`app/models/audit.py:38-39`)
and `rbac_filter.py:285` already filters audit visibility by resource
access. Recommendation: add `organization_id` (Tier-A, nullable —
platform-level audit events like RBAC backfills have no org) and stamp it
from the active org at `audit.record()` time. Audit is security-sensitive
data; deriving it through `target_id` polymorphically is fragile, so this
is a justified denormalization. Platform/system audit rows
(`org IS NULL`) are visible only to platform staff (§4).

### 3.7 Sync / multi-hub (`app/models/sync.py`)

This is a genuine isolation hazard. `outbox_events` already carries
`scope_claims` (`app/models/sync.py:56`) — extend it to include
`organization_id`. The `sync_replicator` applier must:

- Refuse to apply an event whose `organization_id` does not exist locally
  (or create the org first in a controlled way).
- Stamp the org onto every applied row, and run the apply itself under
  the correct tenant scope so a malicious/buggy peer cannot inject a row
  into the wrong org.
- `tombstones` and `sync_cursors` stay Tier-B/global (infra), but the
  applier must check `organization_id` on every payload before write.

This needs explicit test coverage (§3.5.4) — multi-hub sync is the one
path where data crosses a trust boundary by design.

---

## 4. RBAC — rescoping `role_bindings` to org

The existing model (`app/models/role_bindings.py`) is well-built and
**mostly survives**. `scope_type ∈ {global, site, group, device}`
(`role_bindings.py:48-52`); the resolver unions the four sources
(`role_bindings.py:198-251`). Changes:

### 4.1 Users ↔ orgs is M:N — new `organization_memberships` table

A user can belong to more than one org (an MSP managing several customers;
a contractor). So `users` does **not** get an `organization_id` column.
Instead:

```python
class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    id: Mapped[str] = mapped_column(String(40), primary_key=True,
        default=partial(new_id, "om"))
    organization_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False)
    org_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="member")   # owner|admin|member
    created_at: Mapped[datetime] = ts_column()
    __table_args__ = (UniqueConstraint("organization_id", "user_id",
        name="uq_org_membership"),)
```

`org_role` is **org-tier** authority (can you administer the org itself —
invite users, create sites, see billing). It is distinct from the
**resource-tier** `role_bindings` (can you operate device X). An "org
admin" is `org_role='admin'` *plus* an implicit `role_bindings` global-
within-org binding.

### 4.2 `role_bindings` gets `organization_id` and a new top scope

- Add `organization_id` (NOT NULL, FK, Tier-A) to `role_bindings`. A
  binding now lives *inside* an org. Today's `scope_type='global'` means
  "whole portal" — that becomes a security hole the moment there are two
  orgs. **Reinterpret `global` as "global *within this org*"** — i.e. the
  binding's `organization_id` *is* its boundary. A user with a `global`
  binding in org A has zero reach into org B.
- The old portal-wide super-admin (`users.is_super_admin`,
  `ROLE_SUPER_ADMIN`, `role_bindings.py:370-381`) becomes a **platform
  staff** concept, *not* a tenant concept. See §4.4.
- `scope_id` for `site`/`group`/`device` bindings is unchanged, but the
  resolver must additionally confirm the scoped resource belongs to the
  binding's org (defense in depth — the tenant filter already guarantees
  it, but a binding pointing at a foreign site is corrupt data).
- `effective_site_ids` / `effective_device_ids` / `effective_group_ids`
  (`role_bindings.py:177-285`) all run inside a `session_scope()` — once
  the `do_orm_execute` filter is live and the org ContextVar is set,
  their internal `select(Device.id)…` queries are **automatically**
  org-scoped. The resolvers need almost no change; they just stop being
  able to see other orgs' rows. That is a nice property — RBAC and tenant
  isolation compose.

### 4.3 `UniqueConstraint` on `role_bindings`

`uq_role_binding_scope` is `(user_id, scope_type, scope_id)`
(`role_bindings.py:87`). With per-org bindings it becomes
`(organization_id, user_id, scope_type, scope_id)`. The same "NULL
scope_id is distinct" caveat in the existing comment
(`role_bindings.py:82-86`) still applies.

### 4.4 Platform staff (the ex-super-admin)

`super_admin` today is "can do anything on the hub." Under hard
multi-tenancy that is **platform staff** — D. Blagbrough and ops, not a
customer. Recommendation:

- Keep a small `is_platform_staff` boolean on `users` (rename/repurpose
  `users.is_super_admin`, `app/models/users.py:30`). Platform staff are
  the only principals allowed to call `tenant_scope.system()` from a
  request context (e.g. a cross-tenant support console).
- Platform staff are **not** auto-members of every org. Cross-tenant
  access by staff should be an explicit, audited "impersonate org X"
  action (sets the ContextVar to X, writes a loud audit row) — never an
  ambient bypass. This keeps the blast radius of a compromised staff
  account observable.
- A customer-side "org owner" / "org admin" gets full authority **within
  their org** via `org_role` + an org-scoped `global` `role_binding`.

### 4.5 Backwards-compat during migration

The legacy `users.role` / `users.is_admin` columns are still consulted
(`admin_auth.py:119`, `role_bindings.py:377`). They stay authoritative
for *role tier* (admin vs operator vs viewer) — the org work does **not**
touch them. Only the *scope* gains an org dimension. RBAC enforce is still
shadow (`role_bindings.py:302`); the org rollout should **not** be
blocked on the RBAC enforce flip, but the two shadow→enforce cutovers
should be sequenced deliberately (§8) so an operator is never debugging
both at once.

---

## 5. Onboarding — public SaaS signup → org creation → first user as org admin

Today: `signup_requests` (`app/models/signup_requests.py`) is a
*request-access* queue an admin manually approves → an `invitation` is
sent (`app/services/signup_requests.py`, `app/models/invitations.py`).
There is **no self-service org creation** — every user joins the one
implicit hub.

Two onboarding paths must coexist:

### 5.1 Public self-service signup (SaaS)

New flow, new model `app/models/organizations.py` plus reuse of the
signup machinery:

1. Public `/signup` form collects: work email, display name, desired org
   name. Creates a `SignupRequest` **plus** a pending org intent — add
   `requested_org_name` and (after approval) `created_organization_id` to
   `SignupRequest`.
2. **Verify the email** before creating anything real (the hub already
   has `password_resets` / email infra, `app/services/email.py`). A
   verified-email click triggers org creation:
   - Create `Organization` (status `active`, plan `free`, generate
     `slug` from the name with collision suffixing).
   - Create the `User` (email globally unique still holds — see §6 on
     the constraint).
   - Create `OrganizationMembership(org, user, org_role='owner')` and set
     `Organization.owner_user_id`.
   - Create the bootstrapping `role_bindings` row: one
     `(organization_id=org, scope_type='global', scope_id=NULL,
     role='admin')` so the first user can immediately operate everything
     in their new (empty) org. This is the org-scoped analogue of the
     existing bootstrap-admin safety net (`bootstrap.py:298-307`).
   - Audit `organization.created`, `organization.owner_assigned`.
3. Whether self-service signup is **open** or **gated by admin approval**
   is a runtime setting — recommend **gated/invite-only at launch**
   (re-use the `signup_requests` approve flow, just pointing at org
   creation instead of a bare invite), opened up later. This is Q1 for
   the owner.

### 5.2 Inviting users *into an existing org*

`invitations` (`app/models/invitations.py`) already carries
`scope_payload` for RBAC bindings (`app/models/invitations.py:34-37`) and
`role`. Add `organization_id` (NOT NULL — an invite is always into one
org). Redemption creates the `User` (if new) + an `OrganizationMembership`
+ the bindings from `scope_payload`. An invite is issued by an org admin
and can only target their own org (enforced by the tenant filter on the
`invitations` insert). The existing public-invite redemption blueprint
(`app/blueprints/admin/public_invite.py`) is reused.

### 5.3 Self-hosted installs

A self-host deployment is **single-tenant**: exactly one org. The
bootstrap (`bootstrap.py:399` `ensure_bootstrap_admin`) is extended to
also ensure a single `Organization(is_self_hosted_default=True)` exists
and that the bootstrap admin is its owner. The signup/org-creation UI is
hidden when `is_self_hosted_default` org exists and a runtime flag says
"single-tenant mode." This keeps the self-host UX identical to today —
the user never sees the word "organization."

---

## 6. Data migration — existing rows → one default org; and the migration mechanism

### 6.1 The data backfill

Every existing row belongs to **one default org**. This mirrors the
existing one-shot backfill pattern *exactly* — `ensure_role_bindings_backfill`
and `ensure_device_site_id_backfill` (`bootstrap.py:172`, `:320`) are the
template, tracked via a `runtime_settings` row so they run once.

A new `ensure_default_organization_backfill()` in `bootstrap.py`,
tracked by key `organization.default_backfilled_at`:

1. If any `organizations` row exists → done (idempotent).
2. Create one `Organization`:
   - name `"Default Organization"`, slug `"default"`,
     status `active`, plan a new `legacy`/`grandfathered` plan,
     `is_self_hosted_default=True` if the install is single-tenant.
   - `owner_user_id` = the bootstrap admin / first `is_super_admin` user
     (`select(User).where(User.is_super_admin)` — same lookup style as
     `bootstrap.py:405`).
3. `UPDATE` every Tier-A table — set `organization_id` = the default org
   id where `NULL`. One `UPDATE … WHERE organization_id IS NULL` per
   table, exactly like `bootstrap.py:385-391`.
4. Create one `OrganizationMembership(org, user, org_role)` per existing
   user — owner for the bootstrap admin, `admin`/`member` mapped from
   their `users.role`.
5. Set `role_bindings.organization_id` = default org for every existing
   binding (they were all implicitly "this hub" = "this one org").
6. Mark `organization.default_backfilled_at`.

**Ordering matters.** This backfill must run **after** the existing
`ensure_device_site_id_backfill` (so every device has a site, hence a
clear org path) and the column-add step, and **before** the
`organization_id NOT NULL` constraint is applied. Slot it into
`run_startup_bootstrap` (`bootstrap.py:451-477`) between
`ensure_device_site_id_backfill()` and `_ensure_constraints()`.

### 6.2 The schema-migration mechanism — adopt real Alembic now

This is the moment to adopt Alembic properly. Reasoning:

- The current pattern — `create_all()` + a hand-maintained
  `_PENDING_COLUMNS` ADD-COLUMN list (`bootstrap.py:63-128`) +
  `_PENDING_CONSTRAINTS` (`bootstrap.py:143-151`) — has already grown to
  **60+ entries** and cannot express what this change needs:
  - **NOT NULL FK columns on populated tables** must be added nullable,
    backfilled, *then* constrained. `_PENDING_COLUMNS` can only do
    `ADD COLUMN IF NOT EXISTS … <ddl>` blindly (`bootstrap.py:131-137`);
    `_PENDING_CONSTRAINTS` can flip one nullability after a backfill
    (`bootstrap.py:154-164`) — but doing this for ~12 tables with FK
    targets and ordered dependencies is exactly what `_PENDING_*` was
    never designed for.
  - **Renaming/repurposing `users.is_super_admin`** (§4.4) and adding
    unique-constraint changes (§6.3) are DDL that `_PENDING_COLUMNS`
    cannot express at all.
  - It cannot drop a column, cannot reorder, cannot do data migrations
    transactionally with the schema change.
- Alembic is **already configured** — `alembic.ini`, `migrations/env.py`
  wired to `Base.metadata` (`migrations/env.py:9,19`), `script.py.mako`
  present. `migrations/versions/` is simply **empty**
  (`ls migrations/versions/` → only `env.py`, `script.py.mako`). The
  infrastructure cost of adoption is ~zero; only the discipline is new.

**Recommended approach — Alembic for this change, freeze `_PENDING_*`:**

1. **Baseline.** Generate one initial Alembic revision representing the
   *current* schema (`alembic revision --autogenerate` against a
   freshly-`create_all()`'d DB), and `alembic stamp` all existing
   production databases to that baseline. This is the one-time cost.
2. **The org change ships as a sequence of real Alembic revisions:**
   add `organizations` + `organization_memberships` tables → add nullable
   `organization_id` to each Tier-A table → (the data backfill runs) →
   `ALTER … SET NOT NULL` + add FKs + fix unique constraints. Alembic
   handles ordering and is reversible.
3. **Replace the startup `ensure_schema()` create_all path carefully.**
   Do **not** rip it out in the same release — `create_all()` is
   idempotent and harmless. The cutover: `run_startup_bootstrap` runs
   `alembic upgrade head` (programmatically, under the existing
   `pg_advisory_lock`, `bootstrap.py:48`) *instead of*
   `create_all()` + `_ensure_columns()`. Keep `create_all()` only as the
   fresh-DB fast path or drop it once the baseline revision creates
   everything.
4. **Data backfills** (§6.1, and the existing RBAC/site_id ones) can
   either stay as the idempotent `runtime_settings`-tracked functions
   (they work, they're tested) **or** move into Alembic data-migration
   revisions. Recommend: leave the *existing* backfills where they are
   (don't churn working code); write the *new* org backfill as part of
   the Alembic revision sequence so the schema-change + data-change are
   one atomic, ordered, reversible unit. This is the cleanest precedent
   to set going forward.

The `_PENDING_COLUMNS` list stays in place, frozen, for already-shipped
columns on already-deployed DBs that were never Alembic-tracked — it does
no harm (`ADD COLUMN IF NOT EXISTS` is a no-op once the column exists).
New columns from this release on go through Alembic. Document the rule:
**"after the org release, no new entries in `_PENDING_COLUMNS` — write a
migration."**

### 6.3 Unique-constraint changes (must be in the migration)

These global uniques break under multi-tenancy and must become
**per-org** unique constraints in the same migration:

- `users.email` (`app/models/users.py:25`) — **keep global** for v1.
  Email is the login identifier and a user is M:N to orgs; a globally-
  unique email keeps login unambiguous (`authenticate()`,
  `auth.py:19-45`, even does bare-username matching). A user in two orgs
  is *one* `users` row. This is a deliberate exception.
- `sites.name` (`app/models/sites.py:19`) → `UNIQUE(organization_id, name)`.
- `groups.name` (`app/models/groups.py:19`) → `UNIQUE(organization_id, name)`.
- `scenes.name` (`app/models/scenes.py:32`) → `UNIQUE(organization_id, name)`.
- `firmware_releases` `uq_firmware_version_channel`
  (`app/models/firmware.py:45`) — unchanged if firmware stays
  platform-global (§2, Q3).

The backfill (§6.1) runs *before* these constraints are added; since all
existing rows go into one org, no existing pair collides, so the
per-org constraint applies cleanly.

---

## 7. Tier-2 models (api_tokens, webhook channels) carry org scope

The Tier-2 design (`docs/notes/2026-05-20-hub-tier2-design.md`) explicitly
left this open as its Q1 and provisionally specced `site_id` columns
(Tier-2 doc §4a, §6 "Models (new)", "Cross-cutting concerns"). **This
owner decision resolves that Q1: those tables carry `organization_id`
from day one.** Specifically:

- `api_tokens` (Tier-2 §4a) — **Tier-A**: `organization_id` NOT NULL, FK,
  `TenantScoped` mixin. An API token belongs to an org. The token-auth
  resolver (Tier-2 §4a, "extend `middleware/admin_auth.py`") must set the
  tenant ContextVar from the token's `organization_id`, exactly like the
  user and device paths (§3.4). A token's `scopes` (read/write) are
  *within* its org — a token can never reach another org. Keep `site_id`
  too if intra-org site scoping is wanted, but `organization_id` is the
  hard boundary.
- `notification_channels` (Tier-2 §6) — **Tier-A**: `organization_id`
  NOT NULL. A Slack/webhook destination belongs to a tenant.
- `notification_subscriptions` (Tier-2 §6) — **Tier-A**: `organization_id`
  NOT NULL (it references a channel which is org-scoped; carry the column
  for the filter and a defense-in-depth check).
- `webhook_deliveries` (Tier-2 §6) — **Tier-B by derivation**
  (`delivery → channel → org`), **but** this is a high-volume worker
  queue polled by `(status, next_attempt_at)`. Same call as the hot
  Tier-B tables in §2: start derived; denormalize `organization_id` only
  if the worker's query plans need it. The `emit()` function (Tier-2 §6
  `notifications.py`) must run inside the originating org's tenant scope
  so it only ever resolves subscriptions in that org.
- The Tier-2 **SSRF guard** (`ssrf_guard.py`, Tier-2 §6) is org-agnostic
  — it is a network-safety control, unaffected.
- Tier-2 **backup/restore** (Tier-2 §3, `config_backup.py`) becomes
  naturally **per-org** once everything it serializes (rules, schedules,
  scenes, sites, groups, channels) is org-scoped — an export is "this
  org's config." The tenant filter makes that automatic. The backup file
  format should record `source_organization_id`.

**Sequencing note for Tier-2:** the org boundary should land *before* the
Tier-2 API-tokens and webhooks work, or those tables ship with `site_id`
and then need a second migration. If Tier-2 must start first, those three
tables should be built with `organization_id` already, against this
design, even before the rest of the org rollout completes.

---

## 8. Rollout sequencing and risks

### 8.1 Sequencing

Mirror the team's proven RBAC shadow→enforce playbook
(`role_bindings.py:302`, the A1→A8 phases described in
`role_bindings.py:1-26`):

1. **Alembic baseline.** Generate the baseline revision, `alembic stamp`
   every prod DB. No behavior change. (§6.2 step 1.)
2. **Schema, additively.** Migration: create `organizations` +
   `organization_memberships`; add **nullable** `organization_id` to
   every Tier-A table; add the M:N + new role_binding column. Nothing
   reads it yet. Deploy. Zero behavior change.
3. **Backfill.** `ensure_default_organization_backfill()` runs at startup
   (§6.1) — one default org, every row stamped, memberships created.
   Idempotent, `runtime_settings`-tracked. Deploy. Still zero behavior
   change (nothing filters yet).
4. **Constraints.** Once the backfill is confirmed complete on all DBs:
   migration to `SET NOT NULL` + add FKs + swap the per-org unique
   constraints (§6.3).
5. **Tenant filter in SHADOW mode.** Ship `tenant_scope.py`, the
   `do_orm_execute` event, the `before_request` org-binding, the device-
   auth org-binding (§3.4) — but in *count-and-log* mode (§3.5.3): the
   filter computes what it *would* hide and writes `tenant.shadow_diff`
   audit rows, without actually filtering. Run ≥7 days. Any diff is a
   bug to fix *before* enforce.
6. **Enforce flip.** A single runtime-setting toggle
   (`organization.enforce_mode`, modeled on `rbac.enforce_mode`) turns
   the filter from log-only to actually-filter. No redeploy — same
   property the RBAC flip has (`role_bindings.py:296-300`). Watch audit
   for `tenant.enforce_deny` rows.
7. **Onboarding & UI.** Org-switcher, signup/org-creation flow (§5),
   per-org settings. These can land in parallel with 5–6 since they only
   *use* the now-correct data.
8. **Tier-2** (§7) builds on top.
9. **Phase-2 hardening:** add Postgres RLS underneath as defense-in-depth
   (§3.1); consider denormalizing `organization_id` onto the hottest
   tables if query plans demand (§2).

Do **not** run the org enforce flip and the RBAC enforce flip in the same
window — sequence them weeks apart so any incident is attributable.

### 8.2 Risks

- **Isolation bug = data breach.** The whole point of the
  `do_orm_execute` mechanism is that the *default* is safe. The residual
  risk is the **unscoped path** (§3.4): any code that runs with
  `_current_org is None` and is *not* an intentional system bypass. The
  shadow-phase warning audit (§3.5.3) and the static `TenantScoped` test
  (§3.5.1) are the controls. The device API and background jobs are the
  highest-risk call sites — they do not have a `g.current_user` and are
  the easiest to forget.
- **Raw SQL bypasses the ORM filter.** `do_orm_execute` only catches ORM
  `select()`s. The codebase has raw `text()` SQL —
  `bootstrap.py:387` (`UPDATE devices …`), `role_bindings.py:240-248`
  (the v0.5.1 dedupe). Audit every `text(...)` and `conn.execute` for
  tenant-table access. Most are bootstrap/migration code (legitimately
  system-scoped) but each must be confirmed. This is a real gap RLS
  (phase 2) would close completely.
- **Multi-hub sync (§3.7)** crosses a trust boundary by design — a buggy
  or hostile peer could inject cross-org rows. Needs dedicated tests and
  the applier must stamp + verify org on every event.
- **High-volume table joins (§2).** Deriving org for heartbeats/power/
  events through 2–3 joins could regress query latency. Mitigation: index
  the join path; measure post-rollout; denormalize only if needed.
- **The backfill picks the wrong owner.** If the install has no
  `is_super_admin` user, `owner_user_id` is `NULL` and the default org is
  ownerless until an admin claims it. Acceptable (FK is `SET NULL`), but
  the backfill should log loudly and a platform-staff tool should let
  someone assign ownership.
- **Self-host UX regression.** Single-tenant installs must never see the
  word "organization." §5.3 covers it but it needs explicit QA — a
  self-hoster upgrading should notice *nothing*.
- **Connection pool / ContextVar leakage.** If `tenant_scope.reset()` is
  missed in a `teardown`, a pooled worker serves request 2 with request
  1's org — a silent cross-tenant leak. The reset must be in a
  `teardown_request`/`teardown_appcontext` (runs even on exceptions), not
  just `after_request`. Test with a forced exception mid-request.
- **Two shadow→enforce cutovers at once.** Org enforce and RBAC enforce
  are independent state machines; running both flips together makes any
  incident un-debuggable. Sequence them (§8.1).

---

## 9. Questions that genuinely need the product owner

1. **Signup openness.** At launch, is public self-service org creation
   *open* (anyone with a verified email gets an org instantly), or
   *gated* (admin approves each — reuse the existing `signup_requests`
   approve flow)? Recommendation: gated at launch, open later.
2. **One user, many orgs.** Confirm the M:N user↔org model (§4.1) — an
   MSP/contractor can hold accounts in several orgs with one login. The
   alternative (a user belongs to exactly one org, separate accounts per
   org) is simpler but worse UX for the SaaS. Recommendation: M:N.
3. **Firmware catalog scope.** Is firmware (`firmware_releases`) a single
   platform-wide catalog every tenant pulls from, or can a tenant upload
   private firmware? Recommendation: platform-global for v1 — no
   `organization_id` on firmware tables (§2).
4. **`runtime_settings` split.** Confirm the platform-vs-org settings
   split (§2) — a new `organization_settings` table for tenant-editable
   keys (branding, SMTP-from), `runtime_settings` stays platform-global
   (network, RBAC/org enforce mode). Which existing keys are per-org?
5. **Platform staff model.** Confirm `is_super_admin` becomes
   `is_platform_staff` (D. Blagbrough + ops only), and cross-tenant
   support access is an explicit audited "impersonate org" action, not an
   ambient bypass (§4.4).
6. **Org deletion / offboarding.** When a customer leaves, what happens —
   immediate hard delete (cascades), a `closed` status with a retention
   window then purge, or export-then-delete? `Organization.status` has
   the hook; the policy is a product/legal decision. The on-delete
   behaviors in §2 (`RESTRICT` on sites/groups/rules) deliberately make
   accidental org deletion *fail loudly* until this is decided.
7. **Plan caps enforcement.** `max_devices`/`max_users` on `Organization`
   — advisory (checked at create-time, soft) or hard (block at the DB/
   service layer)? Recommendation: advisory in v1.
8. **Tier-2 sequencing.** The Tier-2 API-tokens/webhooks work should land
   *after* this org boundary so those tables are born org-scoped. If
   Tier-2 must start first, confirm it builds those three tables with
   `organization_id` per §7 against this design.
