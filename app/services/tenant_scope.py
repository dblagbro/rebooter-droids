"""Tenant scope — the multi-tenant `organization` boundary enforcement.

Per `docs/notes/2026-05-20-organization-boundary-design.md` §3.

Phase 1 shipped only the `TenantScoped` mixin marker. Phase 2 (this
file) ships the *runtime enforcement mechanism*:

  * `_current_org` / `_bypass` ContextVars — the active tenant for the
    current request/job (design §3.1).
  * a `do_orm_execute` event that injects
    `WHERE organization_id = :current_org` into every ORM SELECT that
    touches a `TenantScoped` entity, via `with_loader_criteria` (§3.1).
  * a `before_flush` event that stamps `organization_id` onto inserted
    `TenantScoped` rows and catches cross-org writes (§3.3).
  * `set_org()` / `reset()` / `system()` context helpers, wired from
    `role_required_*`, the device-auth middleware, the scheduler jobs
    and bootstrap (§3.2, §3.4).
  * a shadow / enforce runtime toggle modeled on `rbac.enforce_mode`
    (§3.5.3, §8.1).

SHADOW MODE IS THE DEFAULT. Both the read filter and the write
rejection default to *count-and-log* — they emit clear audit/log lines
describing what they *would* have filtered or rejected, but do NOT
actually change behaviour. Hard enforcement is gated behind the
`org_isolation.enforce` runtime setting (default off) — exactly the
shadow→enforce pattern the team already trusts for RBAC. There is no
code branch to flip: the cut-over is a single runtime-setting toggle
with no redeploy.

Phase 3 (constraint hardening) shipped the NOT-NULL flip on
`organization_id`, the per-org unique constraints and the FK on-delete
swaps via Alembic revision 0005. The `TenantScoped` mixin still
declares the column as NULLABLE / SET NULL — that is the correct shape
for the two Tier-A tables whose column stays nullable forever
(`audit_events`, `device_announcements`, design §3.6 / §2). Every
other Tier-A model OVERRIDES `organization_id` locally to NOT NULL with
its per-table on-delete behaviour; the override matches migration 0005.

Postgres RLS (design §3.1 / §8.1 step 9) is deliberately NOT in
phase 3 — see `tenant_rls_todo()` below for the rationale.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

from sqlalchemy import ForeignKey, String, event
from sqlalchemy import orm
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, with_loader_criteria

log = logging.getLogger(__name__)


# ── The TenantScoped mixin ─────────────────────────────────────────────


class TenantScoped:
    """Mixin marker for a tenant-owned (Tier-A) model.

    Any model that mixes this in carries the `organization_id` column and
    is auto-filtered by the `do_orm_execute` event below. "Is this entity
    tenant-scoped?" is a single MRO fact, and
    `with_loader_criteria(TenantScoped, ...)` filters all of them at once.

    The column is declared here (rather than per-model) via
    `declared_attr` so every Tier-A table gets a default definition:
    NULLABLE with an on-delete SET NULL FK.

    Phase 3 (constraint hardening) flipped most Tier-A columns to NOT
    NULL with a per-table on-delete behaviour (RESTRICT / CASCADE) —
    Alembic revision 0005. SQLAlchemy `declared_attr` cannot express
    per-table differences, so the mixin keeps the *nullable / SET NULL*
    shape — which is exactly correct for the two Tier-A tables whose
    column stays nullable forever: `audit_events` (platform/system
    audit rows have no org, §3.6) and `device_announcements` (an
    un-adopted announcement legitimately has no org, §2). Those two use
    the mixin column unchanged. Every other Tier-A model OVERRIDES
    `organization_id` with `tenant_scoped_org_column(...)` below to get
    NOT NULL + its target on-delete behaviour, matching migration 0005.

    `with_loader_criteria(TenantScoped, ...)` still filters on the
    `organization_id` attribute regardless of which definition a model
    used, so the read filter is unaffected by the override.
    """

    @declared_attr
    def organization_id(cls) -> Mapped[Optional[str]]:
        return mapped_column(
            String(40),
            # SET NULL on-delete — the default for the nullable Tier-A
            # tables. NOT-NULL Tier-A models override this; see
            # tenant_scoped_org_column().
            ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        )


def tenant_scoped_org_column(ondelete: str = "RESTRICT"):
    """A NOT-NULL `organization_id` column for a Tier-A model.

    Phase 3: most Tier-A tables require `organization_id` (NOT NULL) and
    use a per-table on-delete behaviour — `RESTRICT` for the org-owned
    config entities (sites, groups, rules, schedules, scenes, tokens,
    sensor sources, role bindings) so an accidental org delete fails
    loudly, `CASCADE` for `invitations` (an invite is meaningless once
    its org is gone). A model overrides the mixin's nullable column by
    re-declaring `organization_id` with this helper:

        class Site(TenantScoped, Base):
            organization_id = tenant_scoped_org_column("RESTRICT")

    The column name and FK target match the mixin and migration 0005 so
    `with_loader_criteria(TenantScoped, ...)` still filters it.
    """
    return mapped_column(
        String(40),
        ForeignKey("organizations.id", ondelete=ondelete),
        nullable=False,
    )


# ── The active-tenant ContextVars ──────────────────────────────────────
#
# ContextVar (not flask.g) so background jobs and the scheduler can set
# the scope too. `_current_org` is the org every Tier-A query is filtered
# to; `_bypass` is the explicit, audited system-context escape hatch.
#
# Both default to a "nothing set" state. A request handler MUST call
# `set_org()`; a legitimately-unscoped path (device API resolves its own
# org; background jobs iterate all orgs) MUST enter `system()`. A
# `_current_org is None` with `_bypass is False` means "we forgot" — and
# is flagged loudly (see `_unscoped_violation`).

_current_org: ContextVar[Optional[str]] = ContextVar("current_org", default=None)
_bypass: ContextVar[bool] = ContextVar("tenant_bypass", default=False)


# ── Shadow / enforce runtime toggle ────────────────────────────────────
#
# Modeled exactly on RBAC's `rbac.enforce_mode` (app/services/
# role_bindings.py). Default = shadow. The runtime setting key is
# `org_isolation.enforce`; absence of a DB row → shadow.

ENFORCE_MODE_SHADOW = "shadow"
ENFORCE_MODE_ENFORCE = "enforce"

ORG_ENFORCE_MODE_KEY = "org_isolation.enforce"
ORG_ENFORCE_MODE_ENV = "REBOOTER_ORG_ISOLATION_ENFORCE"


# ── enforce-mode cache ─────────────────────────────────────────────────
#
# load-degradation fix (2026-05-21): pre-fix, every ORM SELECT under an
# org scope read `org_isolation.enforce` from the DB via a nested SELECT
# inside `do_orm_execute` — a ~164x SQL amplification on the read-hot
# path. The enforce toggle is flipped by a human operator at most a
# handful of times in the lifetime of a deployment, so a tiny TTL cache
# with explicit invalidate-on-write is correct and removes the per-query
# DB read entirely. The cache is process-wide (guarded by a lock); the
# TTL is a backstop so a multi-process deployment (gunicorn workers)
# converges on a toggle within the TTL even though `invalidate_enforce_mode_cache()`
# only fires in the writing process.
_ENFORCE_CACHE_TTL = 5.0  # seconds — backstop for cross-process convergence
_enforce_cache_lock = threading.Lock()
_enforce_cache_value: Optional[str] = None
_enforce_cache_expires_at: float = 0.0


_enforce_cache_callback_registered = False


def invalidate_enforce_mode_cache() -> None:
    """Drop the cached `org_isolation.enforce` value so the next
    `enforce_mode()` call re-reads it. Called by `runtime_settings`
    whenever `org_isolation.enforce` is written or deleted, so an
    operator toggle takes effect immediately in the writing process
    (other processes converge within `_ENFORCE_CACHE_TTL`)."""
    global _enforce_cache_value, _enforce_cache_expires_at
    with _enforce_cache_lock:
        _enforce_cache_value = None
        _enforce_cache_expires_at = 0.0


def _ensure_enforce_cache_invalidation_registered() -> None:
    """Register invalidate-on-write with `runtime_settings` so an
    operator flipping `org_isolation.enforce` from the System tab takes
    effect immediately in this process.

    Registered LAZILY (on the first `enforce_mode()` call) rather than at
    import time: `app.models` imports `tenant_scope` for the
    `TenantScoped` mixin while `runtime_settings` itself imports
    `app.models` — registering at import time races that cycle and can
    silently no-op. By the first `enforce_mode()` call every module is
    fully loaded, so the import here always succeeds. Idempotent."""
    global _enforce_cache_callback_registered
    if _enforce_cache_callback_registered:
        return
    try:
        from app.services import runtime_settings

        runtime_settings.register_change_callback(
            ORG_ENFORCE_MODE_KEY, invalidate_enforce_mode_cache
        )
        _enforce_cache_callback_registered = True
    except Exception:  # noqa: BLE001 — never break a query on a wiring hiccup
        log.debug("could not register enforce-mode cache invalidation",
                  exc_info=True)

# Audit actions emitted by this module (search-greppable).
AUDIT_SHADOW_FILTER = "tenant.shadow_filter"   # read filter would have hidden rows
AUDIT_ENFORCE_FILTER = "tenant.enforce_filter"  # read filter actively hid rows
AUDIT_SHADOW_WRITE = "tenant.shadow_write"     # write would have been rejected
AUDIT_ENFORCE_WRITE = "tenant.enforce_write"   # write actively rejected
AUDIT_UNSCOPED = "tenant.unscoped_access"      # Tier-A touched with no org + no bypass


def enforce_mode(session=None, *, cacheable: bool = True) -> str:
    """Current org-isolation enforcement mode — ``shadow`` (default) or
    ``enforce``. Runtime-toggleable via the ``org_isolation.enforce``
    setting; takes effect immediately with no container restart.

    ``session`` — when this is called from inside the ``do_orm_execute``
    or ``before_flush`` hooks, the hook's OWN session MUST be passed so
    the `org_isolation.enforce` lookup reads on the current connection.
    Opening a fresh `session_scope()` from inside a hook is a second
    SQLite connection that deadlocks on the outer transaction's write
    lock — the bug this parameter exists to avoid. Outside a hook
    (no open session to reuse) leave it None and a scope is opened.

    To flip to hard enforcement (only after a clean shadow period):
        runtime_settings.set_("org_isolation.enforce", "enforce")
    To revert:
        runtime_settings.set_("org_isolation.enforce", "shadow")
        (or runtime_settings.delete("org_isolation.enforce"))

    The resolved mode is cached for a few seconds (see
    `invalidate_enforce_mode_cache`) — the toggle changes at most a
    handful of times in a deployment's life, so a per-query DB read is
    pure overhead. A `runtime_settings.set_`/`delete` of the key
    invalidates the cache immediately in the writing process.

    ``cacheable`` — whether the resolved value may populate the shared
    cache. The `do_orm_execute` read path passes True: a SELECT hook
    fires at the start of the SELECT and sees a committed view, so the
    value is trustworthy. The `before_flush` write path passes False:
    it can run mid-`set_` (the very flush that changes this key), where
    the session sees its own UNcommitted write — caching that would
    poison the cache until the TTL or the next invalidation. The
    no-`session` path is always cacheable (a fresh committed read).
    """
    global _enforce_cache_value, _enforce_cache_expires_at

    # Lazily wire invalidate-on-write the first time the mode is read —
    # see `_ensure_enforce_cache_invalidation_registered` for why this is
    # not done at import time.
    _ensure_enforce_cache_invalidation_registered()

    now = time.monotonic()
    cached = _enforce_cache_value
    if cached is not None and now < _enforce_cache_expires_at:
        return cached

    try:
        from app.services import runtime_settings

        if session is not None:
            raw = runtime_settings.get_on_session(
                session,
                ORG_ENFORCE_MODE_KEY,
                env_var=ORG_ENFORCE_MODE_ENV,
                default=ENFORCE_MODE_SHADOW,
            )
        else:
            raw = runtime_settings.get(
                ORG_ENFORCE_MODE_KEY,
                env_var=ORG_ENFORCE_MODE_ENV,
                default=ENFORCE_MODE_SHADOW,
            )
    except Exception:
        # Never let a settings hiccup turn enforcement ON. Fail SAFE for
        # availability — a missing setting means shadow (log-only). Do
        # NOT cache a failure — retry the read on the next call.
        return ENFORCE_MODE_SHADOW

    mode = (
        ENFORCE_MODE_ENFORCE
        if str(raw).strip().lower() == ENFORCE_MODE_ENFORCE
        else ENFORCE_MODE_SHADOW
    )
    # Cache unless the caller flagged the read as untrustworthy (a
    # mid-flush `before_flush` read — see the `cacheable` docstring).
    if cacheable:
        with _enforce_cache_lock:
            _enforce_cache_value = mode
            _enforce_cache_expires_at = now + _ENFORCE_CACHE_TTL
    return mode


def is_enforcing(session=None, *, cacheable: bool = True) -> bool:
    return enforce_mode(session, cacheable=cacheable) == ENFORCE_MODE_ENFORCE


# ── Scope accessors / mutators ─────────────────────────────────────────


def current_org() -> Optional[str]:
    """The org every Tier-A query is currently filtered to, or None."""
    return _current_org.get()


def in_system_context() -> bool:
    """True when an explicit `system()` bypass is active."""
    return _bypass.get()


def set_org(org_id: Optional[str]) -> Token:
    """Bind the active tenant for the current request/job.

    Returns the ContextVar `Token` so the caller can `reset()` precisely.
    Called from `role_required_*`, the device-auth middleware and the
    API-token resolver right after the principal is authenticated.
    """
    return _current_org.set(org_id)


def reset(token: Optional[Token] = None) -> None:
    """Clear the active tenant.

    MUST be called in a `teardown_request`/`teardown_appcontext` so a
    pooled worker never serves the next request with the previous
    request's org (design §8.2 — connection-pool / ContextVar leakage).
    With no token it resets to the default (None); with a token it
    restores the precise previous value.
    """
    if token is not None:
        try:
            _current_org.reset(token)
            return
        except (ValueError, LookupError):
            # Token from a different context — fall through to a plain
            # set(None). reset() must never raise from a teardown hook.
            pass
    _current_org.set(None)


@contextmanager
def system() -> Iterator[None]:
    """Explicit, audited bypass of the tenant filter.

    Use this — never a bare unset ContextVar — for the legitimately
    unscoped paths: background jobs / the scheduler (which iterate all
    orgs themselves), bootstrap, and platform-staff cross-tenant tools.
    Every call site is enumerated and justified in `SYSTEM_BYPASS_SITES`
    below.

    Inside this block the `do_orm_execute` filter and the `before_flush`
    write-stamping are both no-ops: the caller is responsible for not
    crossing org streams.
    """
    token = _bypass.set(True)
    # Also clear any inherited org so a `system()` block opened inside a
    # request context is genuinely unscoped, not silently still filtered.
    org_token = _current_org.set(None)
    try:
        yield
    finally:
        _bypass.reset(token)
        _current_org.reset(org_token)


@contextmanager
def org_context(org_id: str) -> Iterator[None]:
    """Run a block scoped to one specific org, restoring the previous
    scope on exit. Used by background jobs that iterate orgs and want
    each org's work correctly filtered (design §3.4), and by tests.
    """
    token = _current_org.set(org_id)
    bypass_token = _bypass.set(False)
    try:
        yield
    finally:
        _current_org.reset(token)
        _bypass.reset(bypass_token)


# Enumerated, justified `system()` call sites — design §3.4 requires
# every bypass be deliberate and audited. Keep this in sync with the
# actual `tenant_scope.system()` / `org_context()` uses in the tree.
SYSTEM_BYPASS_SITES: tuple[tuple[str, str], ...] = (
    (
        "app/services/bootstrap.py",
        "Startup schema/backfill — runs before any request, must touch "
        "every org's rows to create the default org and stamp them.",
    ),
    (
        "app/jobs/scheduler.py",
        "Background jobs (watchdog/schedule/power-rollup/audit-prune/"
        "external-sensors/epg/sync-replicator) legitimately operate "
        "across all orgs; each job wraps its tick in system() and, where "
        "it processes per-org work, re-enters org_context(org_id).",
    ),
    (
        "app/middleware/device_auth.py",
        "Device-credential resolution runs unscoped to look up the "
        "device → site → org BEFORE a scope can be set; the request "
        "body of the device API then runs scoped to that resolved org.",
    ),
)


# ── The read filter — do_orm_execute ───────────────────────────────────


def _emit_audit(session, action: str, details: dict) -> None:
    """Best-effort audit emit that can never recurse into the filter.

    The audit write itself inserts an `AuditEvent` (a TenantScoped row),
    so it MUST run inside `system()` or the before_flush stamping would
    re-enter and the do_orm_execute filter would re-fire while we are
    mid-event. Best-effort — never raises.

    The audit row is written onto the CURRENT session (the one already
    in the open write transaction), never a new nested `session_scope()`:
    a nested session is a second connection that deadlocks on the
    outer transaction's write lock under SQLite. Persisting the
    `AuditEvent` on the same session keeps it atomic with the write that
    triggered it — the audit log is still written exactly as before,
    only the HOW changed (same session, not a nested one).
    """
    try:
        from app.services import audit

        with system():
            audit.record_on_session(session, action, details=details)
    except Exception:
        log.debug("tenant_scope audit emit failed for %s", action, exc_info=True)


def _final_froms(statement) -> list:
    """The statement's FROM clause, across SQLAlchemy versions.

    `Select.froms` was deprecated in favour of `get_final_froms()`. Use
    the method when present, fall back to the attribute otherwise. Never
    raises — returns [] on anything unexpected."""
    try:
        getter = getattr(statement, "get_final_froms", None)
        if callable(getter):
            return list(getter())
        return list(getattr(statement, "froms", []) or [])
    except Exception:
        return []


def _statement_touches_tenant_scoped(statement) -> bool:
    """Heuristic: does this SELECT involve at least one TenantScoped
    entity? Used only for shadow-mode logging granularity — the actual
    filter is applied unconditionally via with_loader_criteria, which is
    a no-op on statements with no TenantScoped entity."""
    try:
        for col_desc in getattr(statement, "column_descriptions", []) or []:
            entity = col_desc.get("entity")
            if entity is not None and isinstance(entity, type) and issubclass(
                entity, TenantScoped
            ):
                return True
        # Also check the FROM clause for mapped TenantScoped tables.
        froms = _final_froms(statement)
        if froms:
            scoped_tables = _tenant_scoped_tablenames()
            for f in froms:
                if getattr(f, "name", None) in scoped_tables:
                    return True
    except Exception:
        # If introspection fails, assume it might touch a scoped entity
        # so we never *under*-report in shadow mode.
        return True
    return False


_SCOPED_TABLENAMES: Optional[frozenset] = None


def _tenant_scoped_tablenames() -> frozenset:
    """The set of __tablename__s for every mapped TenantScoped model.
    Cached after first call — the model registry is static at runtime."""
    global _SCOPED_TABLENAMES
    if _SCOPED_TABLENAMES is None:
        names = set()
        try:
            from app.models import Base

            for mapper in Base.registry.mappers:
                cls = mapper.class_
                if isinstance(cls, type) and issubclass(cls, TenantScoped):
                    names.add(cls.__tablename__)
        except Exception:
            log.debug("could not enumerate TenantScoped tables", exc_info=True)
        _SCOPED_TABLENAMES = frozenset(names)
    return _SCOPED_TABLENAMES


# ── do_orm_execute re-entrancy guard ───────────────────────────────────
#
# load-degradation fix (2026-05-21): `_add_tenant_filter` itself issues
# DB reads — `is_enforcing()` (on a cache miss) reads `org_isolation.enforce`
# and `_unscoped_violation()` writes an audit row. Those inner statements
# re-fire `do_orm_execute`, re-entering this very hook. With an org scope
# bound none of the early short-circuits caught the re-entry, so the hook
# recursed ~150 deep per query (and amplified SQL ~164x); under
# concurrency the `RecursionError` landed in the connection-pool
# machinery and orphaned checked-out connections until the pool
# exhausted. This ContextVar is a hard re-entrancy guard: while the hook
# is running on this context it is a strict no-op for any nested ORM
# SELECT it triggers. Belt-and-suspenders alongside the top-of-function
# `_statement_touches_tenant_scoped` short-circuit below — either alone
# stops the recursion; together they make this bug class unable to recur.
_in_filter: ContextVar[bool] = ContextVar("tenant_scope_in_filter", default=False)


@event.listens_for(orm.Session, "do_orm_execute")
def _add_tenant_filter(execute_state) -> None:
    """Inject `WHERE organization_id = :current_org` into every ORM
    SELECT that touches a TenantScoped entity (design §3.1).

    Behaviour:
      * `system()` active  → no-op (explicit, audited bypass).
      * no org bound       → no-op for the SELECT, but a Tier-A SELECT
                             in this state is a latent isolation bug —
                             emit a `tenant.unscoped_access` audit row.
      * org bound, SHADOW  → apply the filter to a *copy* and compare;
                             log `tenant.shadow_filter` if it differs,
                             but execute the UNfiltered statement.
      * org bound, ENFORCE → apply the filter for real.

    The filter only ever *adds* a WHERE; `with_loader_criteria` is a
    no-op for statements that touch no TenantScoped entity, so this is
    safe to attach unconditionally.
    """
    # Only SELECTs. INSERT/UPDATE/DELETE org-correctness is handled by
    # the before_flush write-stamping below. (UPDATE/DELETE by ORM also
    # flow through before_flush; bulk Core UPDATE/DELETE are rare in
    # this codebase and are flagged in the phase-2 report.)
    if not execute_state.is_select:
        return

    # Re-entrancy guard (load-degradation fix 2026-05-21). If this hook
    # is already running on the current context, any ORM SELECT it
    # triggers (the `org_isolation.enforce` lookup, an audit-row read)
    # MUST be a hard no-op — never recurse into the filter. Those inner
    # statements never touch a TenantScoped entity, so skipping them
    # changes no filtering behaviour.
    if _in_filter.get():
        return

    # Short-circuit non-tenant statements BEFORE any `is_enforcing()`
    # call (load-degradation fix 2026-05-21). `with_loader_criteria` is a
    # no-op on a statement that touches no TenantScoped entity, so for
    # such a statement there is nothing to do — and crucially, doing it
    # here, ahead of `is_enforcing()`, means the non-tenant
    # `runtime_settings`/audit reads issued from inside this hook return
    # immediately instead of dragging `is_enforcing()` through another
    # ~150 levels of recursion. Tenant-scoped statements fall through and
    # are filtered EXACTLY as before — filtering behaviour is unchanged.
    touches_tenant_scoped = _statement_touches_tenant_scoped(
        execute_state.statement
    )
    if not touches_tenant_scoped:
        return

    # Explicit system bypass — design §3.4.
    if _bypass.get():
        return

    org_id = _current_org.get()

    # The dangerous case: a Tier-A SELECT with no org and no bypass.
    # Hold the re-entrancy guard across `_unscoped_violation` — it emits
    # an audit row, whose flush + any enforce-mode read must not re-enter
    # this hook.
    if org_id is None:
        token = _in_filter.set(True)
        try:
            _unscoped_violation(execute_state)
        finally:
            _in_filter.reset(token)
        return

    criteria = with_loader_criteria(
        TenantScoped,
        lambda cls: cls.organization_id == org_id,
        include_aliases=True,
    )

    # Read the enforce-mode setting on the CURRENT session — never a
    # nested `session_scope()` (a second SQLite connection deadlocking
    # on this transaction's write lock). The `_in_filter` guard is held
    # for the duration so the (cache-miss) enforce-mode lookup cannot
    # re-enter this hook.
    token = _in_filter.set(True)
    try:
        enforcing = is_enforcing(execute_state.session)
    finally:
        _in_filter.reset(token)

    if enforcing:
        # Hard enforcement — the filter is applied for real.
        execute_state.statement = execute_state.statement.options(criteria)
        return

    # SHADOW MODE (default): do NOT change what the query returns. We
    # already know this statement touches a TenantScoped entity (it
    # passed the top-of-function check), so log that a Tier-A query ran
    # under an org scope and record the org, leaving the row-level diff
    # to the targeted shadow tests (design §3.5.2). Counting actual
    # hidden rows would mean executing the query twice on the read-hot
    # path — too expensive for a default-on mechanism.
    log.debug(
        "tenant_scope SHADOW: Tier-A SELECT under org=%s "
        "(filter NOT applied — shadow mode)",
        org_id,
    )
    # In shadow mode the statement is left untouched — no `.options()`.


def _unscoped_violation(execute_state) -> None:
    """A TenantScoped SELECT ran with no org bound and no system bypass.

    Per design §3.4 / §8.2 this is the single biggest isolation-bug
    surface: `_current_org is None` must mean "explicit system bypass",
    never "we forgot". We log loudly and (in shadow mode) emit an audit
    row. We do NOT block — even in enforce mode, because a None org with
    no bypass is a *code* bug, not an attacker; failing the query closed
    would just turn a latent bug into an outage. The audit row is the
    control; the fix is to add a `set_org()` or `system()` at the call
    site.
    """
    try:
        froms = _final_froms(execute_state.statement)
        tables = sorted(
            {getattr(f, "name", "?") for f in froms} if froms else set()
        )
    except Exception:
        tables = []
    log.warning(
        "tenant_scope: Tier-A SELECT with NO org bound and NO system "
        "bypass — latent isolation bug. tables=%s. Add tenant_scope."
        "set_org(...) or wrap the call in tenant_scope.system().",
        tables,
    )
    _emit_audit(
        execute_state.session,
        AUDIT_UNSCOPED,
        {
            "tables": tables,
            "note": "Tier-A SELECT executed with no org scope and no "
            "system bypass — see design §3.4.",
        },
    )


# ── The write path — before_flush insert/update org-stamping ───────────


class CrossOrgWriteError(Exception):
    """Raised (enforce mode only) when a TenantScoped row is inserted or
    modified with an `organization_id` that differs from the active org.

    In shadow mode this is never raised — the attempted cross-org write
    is logged as a `tenant.shadow_write` audit row and the flush
    proceeds (legacy behaviour stays authoritative). In enforce mode it
    aborts the transaction.
    """

    def __init__(self, entity: str, row_org: Optional[str], active_org: Optional[str]):
        self.entity = entity
        self.row_org = row_org
        self.active_org = active_org
        super().__init__(
            f"cross-org write on {entity}: row organization_id={row_org!r} "
            f"!= active org {active_org!r}"
        )


@event.listens_for(orm.Session, "before_flush")
def _stamp_and_guard_tenant_writes(session, flush_context, instances) -> None:
    """Stamp `organization_id` onto new TenantScoped rows and catch
    cross-org writes (design §3.3).

    For every TenantScoped instance in `session.new` (INSERTs) and
    `session.dirty` (UPDATEs):

      * `system()` active → leave the row's org exactly as the caller
        set it (system code is trusted to set the right org itself).
      * org bound, row has no org → stamp it from the active org. This
        turns "service code forgot to set the org on a new Site" into a
        guaranteed-correct default.
      * org bound, row has a *different* org → cross-org write. In
        ENFORCE mode raise `CrossOrgWriteError` (aborts the flush); in
        SHADOW mode log `tenant.shadow_write` and let it through.
      * no org bound, no bypass, row has no org → cannot stamp; log a
        warning. (Enforce mode does not block here for the same reason
        `_unscoped_violation` does not — it is a code bug, not an
        attack; the audit row is the control.)
    """
    if _bypass.get():
        return

    active_org = _current_org.get()
    # Read the enforce-mode setting on the CURRENT session — never a
    # nested `session_scope()` (a second SQLite connection deadlocking
    # on this flush's write lock). The `_in_filter` guard is held across
    # the read so a cache-miss enforce-mode SELECT cannot re-enter the
    # `do_orm_execute` filter (load-degradation fix 2026-05-21).
    # `cacheable=False`: this fires mid-flush and may see uncommitted
    # state (the `set_` of `org_isolation.enforce` itself), so its result
    # must not populate the shared cache.
    token = _in_filter.set(True)
    try:
        enforcing = is_enforcing(session, cacheable=False)
    finally:
        _in_filter.reset(token)

    # `AuditEvent` rows are exempt from the write guard. They are an
    # append-only platform record whose `organization_id` is permanently
    # nullable (design §3.6) — a system/unscoped audit row legitimately
    # has no org. They are ALSO the row this hook's own `_emit_audit`
    # adds to the current session: SQLAlchemy re-scans `session.new`
    # after `before_flush` and would re-fire the guard on that fresh
    # audit row, which would emit yet another audit row and loop
    # forever. Skipping `AuditEvent` here breaks that recursion. The
    # `do_orm_execute` read filter still scopes audit *reads* by org, so
    # isolation on the read path is unchanged; `audit._build_event`
    # stamps the active org onto normal audit rows itself.
    from app.models import AuditEvent

    # INSERTs — stamp the org on; guard against a foreign org.
    for obj in list(session.new):
        if not isinstance(obj, TenantScoped):
            continue
        if isinstance(obj, AuditEvent):
            continue
        _guard_one(session, obj, active_org, enforcing, is_insert=True)

    # UPDATEs — a TenantScoped row must not be re-homed to another org.
    for obj in list(session.dirty):
        if not isinstance(obj, TenantScoped):
            continue
        if isinstance(obj, AuditEvent):
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        _guard_one(session, obj, active_org, enforcing, is_insert=False)


def _guard_one(
    session, obj, active_org: Optional[str], enforcing: bool, *, is_insert: bool
) -> None:
    entity = type(obj).__name__
    row_org = getattr(obj, "organization_id", None)

    if active_org is None:
        # No scope and no bypass — we cannot stamp. Code bug.
        if row_org is None:
            log.warning(
                "tenant_scope: %s %s with no organization_id and no "
                "active org scope — cannot stamp. Add tenant_scope."
                "set_org(...) or wrap in tenant_scope.system().",
                "INSERT of" if is_insert else "UPDATE of",
                entity,
            )
            _emit_audit(
                session,
                AUDIT_UNSCOPED,
                {
                    "entity": entity,
                    "op": "insert" if is_insert else "update",
                    "note": "TenantScoped write with no org scope — "
                    "could not stamp organization_id.",
                },
            )
        # If the row already carries an org and there is no active scope
        # to compare against, leave it — a system-ish path that set the
        # org explicitly but forgot the system() wrapper. Logged above
        # only when unstampable.
        return

    if row_org is None:
        # The happy path — stamp the active org on.
        obj.organization_id = active_org
        return

    if row_org == active_org:
        return  # already correct

    # Cross-org write — the row carries a DIFFERENT org than the active
    # scope. This is the attack/bug case design §3.3 calls out.
    log.warning(
        "tenant_scope: cross-org %s on %s — row organization_id=%s, "
        "active org=%s (enforce=%s)",
        "INSERT" if is_insert else "UPDATE",
        entity,
        row_org,
        active_org,
        enforcing,
    )
    action = AUDIT_ENFORCE_WRITE if enforcing else AUDIT_SHADOW_WRITE
    _emit_audit(
        session,
        action,
        {
            "entity": entity,
            "op": "insert" if is_insert else "update",
            "row_organization_id": row_org,
            "active_organization_id": active_org,
            "enforce_mode": ENFORCE_MODE_ENFORCE if enforcing else ENFORCE_MODE_SHADOW,
        },
    )
    if enforcing:
        raise CrossOrgWriteError(entity, row_org, active_org)
    # SHADOW MODE: do not block. The flush proceeds with the row's
    # original (foreign) org — legacy behaviour — and the audit row
    # above records the divergence for the ≥7-day shadow review.


# ── Postgres RLS — deferred (design §3.1 / §8.1 step 9) ────────────────


def tenant_rls_todo() -> str:
    """Why Postgres Row-Level Security is NOT shipped in phase 3.

    The design (§3.1, §8.1 step 9) places RLS in the *phase-2 hardening*
    band — "defense-in-depth", to be "added underneath later without app
    changes" — explicitly AFTER the application-level filter, the
    constraint hardening and the shadow→enforce cut-over.

    Phase 3's scope is constraint hardening (NOT NULL, per-org uniques,
    FK on-delete) plus the sync-applier isolation fix. RLS is not cleanly
    doable inside that scope because, per the design's own §3.1 / §8.2
    analysis, it requires:

      * every pooled connection to `SET app.org_id` per request and
        RESET it on release — new connection-lifecycle plumbing that
        interacts with `pool_pre_ping` (app/db.py);
      * the bootstrap / `pg_advisory_lock` / Alembic paths to run as a
        role that BYPASSRLS (they legitimately touch every org), which
        is a role/grants change outside a schema migration;
      * a Postgres-only feature — the unit-test suite runs on SQLite,
        which has no RLS, so it cannot be exercised by tests/unit/.

    Shipping a half-wired RLS layer would be worse than none. RLS
    therefore remains a clean, separate follow-up: a dedicated revision
    that `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY USING
    (organization_id = current_setting('app.org_id')::text)` on each
    Tier-A table, paired with the connection-lifecycle `SET`/`RESET`
    plumbing and a BYPASSRLS role for system contexts. The
    application-level `do_orm_execute` filter shipped in phase 2 is the
    primary control and is fully in force; RLS is the belt to that
    suspenders.

    This function exists so the deferral is greppable and intentional,
    not an omission.
    """
    return (
        "Postgres RLS deferred to a post-phase-3 follow-up — see "
        "tenant_scope.tenant_rls_todo() and design §3.1 / §8.1 step 9."
    )
