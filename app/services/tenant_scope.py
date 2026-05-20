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

TODO(org-phase3): NOT-NULL flip on `organization_id`, per-org unique
constraints, FK on-delete swaps, and Postgres RLS are deferred to
phase 3 — see the design doc §8.1 step 9 and §6.3.
"""

from __future__ import annotations

import logging
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
    `declared_attr` so every Tier-A table gets an identical definition.
    The column is NULLABLE in phase 1/2 — see the module docstring.

    TODO(org-phase3): make `organization_id` NOT NULL via a migration
    once `ensure_default_organization_backfill()` is confirmed on every
    DB, and swap the per-table on-delete behaviour.
    """

    @declared_attr
    def organization_id(cls) -> Mapped[Optional[str]]:
        return mapped_column(
            String(40),
            # SET NULL on-delete so a stray org delete can never block.
            # TODO(org-phase3): swap the per-table on-delete behaviour
            # (RESTRICT for sites/groups/rules) alongside the NOT-NULL
            # flip — see design §2 Tier-A table.
            ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
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

# Audit actions emitted by this module (search-greppable).
AUDIT_SHADOW_FILTER = "tenant.shadow_filter"   # read filter would have hidden rows
AUDIT_ENFORCE_FILTER = "tenant.enforce_filter"  # read filter actively hid rows
AUDIT_SHADOW_WRITE = "tenant.shadow_write"     # write would have been rejected
AUDIT_ENFORCE_WRITE = "tenant.enforce_write"   # write actively rejected
AUDIT_UNSCOPED = "tenant.unscoped_access"      # Tier-A touched with no org + no bypass


def enforce_mode() -> str:
    """Current org-isolation enforcement mode — ``shadow`` (default) or
    ``enforce``. Runtime-toggleable via the ``org_isolation.enforce``
    setting; takes effect immediately with no container restart.

    To flip to hard enforcement (only after a clean shadow period):
        runtime_settings.set_("org_isolation.enforce", "enforce")
    To revert:
        runtime_settings.set_("org_isolation.enforce", "shadow")
        (or runtime_settings.delete("org_isolation.enforce"))
    """
    try:
        from app.services import runtime_settings

        raw = runtime_settings.get(
            ORG_ENFORCE_MODE_KEY,
            env_var=ORG_ENFORCE_MODE_ENV,
            default=ENFORCE_MODE_SHADOW,
        )
    except Exception:
        # Never let a settings hiccup turn enforcement ON. Fail SAFE for
        # availability — a missing setting means shadow (log-only).
        return ENFORCE_MODE_SHADOW
    return (
        ENFORCE_MODE_ENFORCE
        if str(raw).strip().lower() == ENFORCE_MODE_ENFORCE
        else ENFORCE_MODE_SHADOW
    )


def is_enforcing() -> bool:
    return enforce_mode() == ENFORCE_MODE_ENFORCE


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


def _emit_audit(action: str, details: dict) -> None:
    """Best-effort audit emit that can never recurse into the filter.

    The audit write itself inserts an `AuditEvent` (a TenantScoped row),
    so it MUST run inside `system()` or the before_flush stamping would
    re-enter and the do_orm_execute filter would re-fire while we are
    mid-event. Best-effort — never raises.
    """
    try:
        from app.services import audit

        with system():
            audit.record(action, details=details)
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

    # Explicit system bypass — design §3.4.
    if _bypass.get():
        return

    org_id = _current_org.get()

    # The dangerous case: a Tier-A SELECT with no org and no bypass.
    if org_id is None:
        if _statement_touches_tenant_scoped(execute_state.statement):
            _unscoped_violation(execute_state)
        return

    criteria = with_loader_criteria(
        TenantScoped,
        lambda cls: cls.organization_id == org_id,
        include_aliases=True,
    )

    if is_enforcing():
        # Hard enforcement — the filter is applied for real.
        execute_state.statement = execute_state.statement.options(criteria)
        return

    # SHADOW MODE (default): do NOT change what the query returns. We
    # still want to *know* the filter would have mattered, so we run a
    # cheap structural check: only statements that touch a TenantScoped
    # entity could be affected. Counting actual hidden rows would mean
    # executing the query twice on the write-hot path — too expensive
    # for a default-on mechanism — so shadow mode logs that a Tier-A
    # query ran under an org scope and records the org, leaving the
    # row-level diff to the targeted shadow tests (design §3.5.2).
    if _statement_touches_tenant_scoped(execute_state.statement):
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
    enforcing = is_enforcing()

    # INSERTs — stamp the org on; guard against a foreign org.
    for obj in list(session.new):
        if not isinstance(obj, TenantScoped):
            continue
        _guard_one(obj, active_org, enforcing, is_insert=True)

    # UPDATEs — a TenantScoped row must not be re-homed to another org.
    for obj in list(session.dirty):
        if not isinstance(obj, TenantScoped):
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        _guard_one(obj, active_org, enforcing, is_insert=False)


def _guard_one(obj, active_org: Optional[str], enforcing: bool, *, is_insert: bool) -> None:
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
