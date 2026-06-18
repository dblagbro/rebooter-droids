# Refactor Log

Append-only journal of structural changes. Newest at top. Format:

```
## YYYY-MM-DD — <one-line scope>
- Branch: <branch-name>
- Releases included: vX.Y.Z[, vX.Y.Z, …]
- Key changes: bullet list
- Architectural decisions: bullet list (link to architecture.md
  sections that were updated)
- Files impacted: short summary (counts > exhaustive list)
- Risks: ...
- Remaining issues: ...
- Next recommended: ...
```

---

## 2026-06-17 — split `services/watchdog.py` into `services/watchdog/` subpackage

- **Branch:** `main` (single atomic commit)
- **Releases included:** v0.6.48
- **Scope:** behavior-preserving refactor — single largest
  non-subpackaged service module (`services/watchdog.py` at 952 LOC,
  ≈2× the 500-LOC soft limit) split into a 4-way subpackage. Mirrors
  the precedent set by `services/devices/`,
  `services/watchdog_runtime/`, and `services/external_sensors/`.
- **Key changes:**
  - New subpackage `app/services/watchdog/` with four cohesive slices:
    - `_render.py` (225 LOC) — pure presentation; renders the
      operator-facing plain-English rule sentence (R-WD-1). No
      `session_scope()`, no model writes.
    - `_validate.py` (340 LOC) — per-kind probe + action validators +
      typed `WatchdogValidationError`. This is the busiest churn axis
      of the service (every new probe / action kind adds rules here).
    - `_query.py` (175 LOC) — read-only queries + serializer
      (`list_rules`, `get_rule`, `list_rules_for_device`,
      `list_recent_events`, `probe_now`, `serialize_rule`, `_iso`).
    - `_mutations.py` (220 LOC) — writes (`create_rule`,
      `update_rule`, `delete_rule`, `set_enabled`) sharing a new
      private `_validate_rule_inputs()` pre-flight helper.
  - Public API surface preserved via `__init__.py` re-exports +
    `__all__` declaration. Every existing
    `from app.services.watchdog import …` resolves unchanged.
  - Back-compat aliases (`_validate_probe`, `_validate_action`) at
    the package root for the one test that imports the underscore-
    prefixed names; promote to public names in a follow-up cleanup.
  - Hidden incidental win: the create_rule + update_rule validation
    blocks were duplicated verbatim (~30 lines each); consolidated
    into a single `_validate_rule_inputs()` helper called from both,
    eliminating the source-of-truth drift risk that was already
    biting BUG-035 / BUG-036 / BUG-038 / BUG-055.
  - Old `app/services/watchdog.py` deleted in the same commit.
  - `docs/architecture.md` updated: source-layout tree gains the
    `watchdog/` entry, and the "Service subpackages" section gains
    `watchdog/` as the fourth precedent with notes on the
    validation-churn-axis rationale.
- **Architectural decisions:**
  - Slice by *responsibility*, not by HTTP-surface or read/write
    alone — `_render` and `_validate` are pulled out because they
    each evolve along independent axes (presentation, validation
    registry). Read/write split for the rest matches the established
    `devices/` precedent.
  - Validation gets its own slice rather than living next to writes
    (which would have been the simpler split): the 340-LOC validator
    is half the file's size and is the highest-churn axis. Future
    probe-kind additions touch only `_validate.py`, not the writes.
  - `_render` is pure (no DB) so it has zero imports from elsewhere
    in the subpackage. Module-level imports are one-directional
    (`_query → _render`, `_mutations → _query + _validate + _render`).
    No cycles, no deferred imports needed inside this subpackage.
  - Per-slice file sizes (175–340 LOC) are well under the 500-LOC
    soft limit; the slowest-growing axis (mutations) gets the
    smallest file so future additions land cleanly without
    re-splitting.
- **Files impacted:**
  - 1 file deleted: `app/services/watchdog.py` (952 LOC)
  - 5 files added: `app/services/watchdog/{__init__,_render,
    _validate,_query,_mutations}.py` (1015 LOC total, +63 LOC for
    re-exports + the duplicated-validation-block consolidation
    comment overhead, offset by ~30 LOC saved in
    `_validate_rule_inputs()`).
  - 2 docs updated: `docs/architecture.md` (source layout +
    Service subpackages precedents), `docs/refactor-log.md` (this
    entry).
  - 0 consumer files touched. Every
    `from app.services.watchdog import …` continues to resolve.
- **Risks:**
  - Import-path drift: any code that imported the legacy
    `_validate_probe`, `_validate_action`, or `_validate_leaf`
    underscore symbols would break. Mitigated by keeping
    `_validate_probe = validate_probe` and
    `_validate_action = validate_action` aliases at the package
    root. The one test grep'd at the time (`test_probe_kind_registry.py`)
    uses `_validate_probe` and passes against the new layout.
  - Behavior preservation: the validation block was lifted verbatim
    out of `create_rule` + `update_rule` into the shared helper. Any
    bug introduced would show as a diff in the error-message string
    or the ordering of which check fires first. The 760-test unit
    suite includes 159 watchdog-targeted tests — all green pre and
    post.
- **Remaining issues:**
  - `services/watchdog_runtime/_probes_integrations.py` (1053 LOC)
    is now the single largest file in `services/`. It is already
    inside a subpackage and is shaped as one pure `_probe_*` per
    integration kind with no shared state, so the case for splitting
    further (by integration kind: roku / ha / weather / ical / power
    / solar / snmp / media / webhook / mqtt / epg) is weaker than
    `watchdog.py`'s was — splitting would add 11 files for marginal
    cohesion benefit. Defer.
  - `blueprints/admin/devices_ui.py` (840 LOC) is the largest
    blueprint, but the "co-locate UI + API per feature" rule
    (architecture.md §"Module-boundary principles") explicitly
    forbids splitting it further. Re-evaluate only if it crosses
    1000 LOC.
  - `services/config_backup.py` (977 LOC) is the next-largest
    non-subpackaged service. Lower iteration than watchdog (it's
    operational tooling, not user-feature surface), so the case for
    splitting is weaker. Re-evaluate when it next grows.
- **Next recommended targets:**
  1. Promote the back-compat `_validate_probe` / `_validate_action`
     aliases to a public name migration in tests, then delete the
     aliases. Two-step: update test imports, ship, delete aliases.
  2. Consolidate the duplicated picker-validation pattern (BUG-074
     fixed it in `schedules.py`; the same shape lives in
     `blueprints/admin/_rules_forms.py` build_target_from_form and
     `groups.py` device-picker render). Extract a
     `middleware/picker_scope.py::validate_picker_id(form_value,
     visible_ids, *, scope_label)` helper that all three handlers
     call. Closes the latent BUG-064 vector on the remaining
     pickers.
  3. If `config_backup.py` grows past 1000 LOC, split into a
     subpackage along the take/restore/list axes (similar to
     `external_sensors/` ingestion-shape split).

---

## 2026-05-15 — extract rule-form mapping out of the rules blueprint

- **Branch:** `main` (single atomic commit)
- **Releases included:** v0.5.67
- **Scope:** small, behavior-preserving refactor — one over-limit
  blueprint brought back under the soft limit by extracting business
  logic, not by splitting HTTP surfaces.
- **Why:** `blueprints/admin/rules.py` was 645 LOC (1.3× the 500-LOC
  blueprint soft limit). The cause was a single **211-line handler**,
  `rules_create_submit` — ~130 lines of which were a `probe_kind`
  `if/elif` building the probe JSON dict from form fields. That is
  mapping/business logic, which `architecture.md` §"Module-boundary
  principles" explicitly bars from blueprints.
- **Key changes:**
  - New `app/blueprints/admin/_rules_forms.py` (219 LOC) — four pure
    builders (`build_probe_from_form`, `build_target_from_form`,
    `build_action_from_form`, `build_maintenance_windows_from_form`)
    + a typed `RuleFormError`.
  - `rules_create_submit` rewritten 211 → ~32 LOC: call the four
    builders inside one `try`, catch `RuleFormError` → flash +
    redirect (identical operator-facing behavior), then
    `svc_create_rule` as before.
  - `rules.py`: 645 → 487 LOC — under the 500 soft limit.
- **Architectural decisions:**
  - **Extract logic, do NOT split by HTTP surface.** The naive
    "split UI from API" suggested in the prior log entry's
    next-targets list would have *contradicted* the co-location
    principle (one feature = one `blueprints/admin/<x>.py` holding
    both UI + API). The real fix for an over-limit blueprint is to
    move the *non-HTTP logic* out — here, into a blueprint-adjacent
    `_rules_forms.py` helper (form input is presentation-layer, so it
    stays in `blueprints/admin/`, not the service layer). The
    next-targets note in the prior entry is corrected accordingly.
  - **Builders raise, handlers flash.** A builder signals bad input
    with `RuleFormError`; the HTTP concern (flash + redirect) stays
    in the blueprint. Keeps the builders pure + unit-testable.
- **Files impacted:**
  - 1 file created (`_rules_forms.py`), 1 modified (`rules.py`)
  - 3 docs: `CHANGELOG.md`, `refactor-log.md` (this entry); no
    `architecture.md` change needed (no module boundary moved — a
    blueprint-internal helper, same as `_common.py`)
  - 0 template / service / route changes — URLs + behavior preserved
- **Risks:**
  - Low. Pure extraction; the probe/target/action JSON shapes are
    byte-identical to the inline code. Verified with a `create_app()`
    smoke test in the built image + the rules route still resolving.
  - One pre-existing latent issue carried over unchanged: a
    non-numeric `power_off_seconds` / threshold-int field still
    raises an uncaught `ValueError` (HTTP 500). Not introduced here;
    fixing it would be a behavior *change*, so deferred.
- **Remaining technical debt / next targets:**
  1. `blueprints/admin/settings.py` (596 LOC) — over the limit;
     extract the per-tab save logic if it has the same handler-bloat
     smell.
  2. `blueprints/admin/devices_ui.py` (563 LOC) — same class.
  3. `services/device_power.py` (723) — split when P1/P3 power work
     next extends it.
  4. Stale probe-shape reference card in `templates/rules/edit.html`
     (~7 of ~25 kinds documented).
  5. Underscore-prefixed cross-module helpers — still un-promoted
     (carried from the 2026-05-14 entry).

---

## 2026-05-15 — oversized-service split: external_sensors + watchdog probes

- **Branch:** `main` (single atomic commit)
- **Releases included:** v0.5.65
- **Scope:** moderate, behavior-preserving refactor — the two service
  files that ballooned during the B16/B17 integration arc split into
  cohesive modules. Pure re-organization; zero behavior change.
- **Why:** across v0.5.50–v0.5.64 the session added 7 integration
  kinds and ~13 watchdog probes, all into two files. They reached
  1369 and 1265 LOC — 5.5× and ~5× the soft limits — and every new
  integration made them worse. Highest-value targets per the
  maintainability/dev-speed lens.
- **Key changes:**
  - `app/services/external_sensors.py` (1369 LOC) →
    `app/services/external_sensors/` subpackage:
    - `__init__.py` — public API re-exports (13 functions + `_iso`);
      every `from app.services.external_sensors import …` and
      `from app.services import external_sensors as ext_svc` site
      keeps resolving (5 importer sites).
    - `_common.py` — `_iso`, `ROKU_DEFAULT_PORT` — dependency-free
      shared leaf.
    - `_crud.py` — source registry: `create_source`, `list_sources`,
      `set_enabled`, `delete_source`, `_validate_kind_config`,
      `_serialize`, `_redact_config`.
    - `_pollers.py` — `poll_source`, `poll_all_due`, `_poll_kind` +
      all `_poll_<kind>` (roku/HA/weather/iCal/solar×2/SNMP) + SNMP
      CLI helpers + poll constants/OIDs/regexes.
    - `_inbound.py` — `record_webhook_event`, `record_mqtt_message`.
    - `_query.py` — `latest_sample`, `latest_sample_for_topic`,
      `last_two_samples`, `ha_entities`, `latest_active_app`.
  - `app/services/watchdog_runtime/_probes.py` (1265 LOC) → split:
    - `_probes.py` keeps `run_probe()` + the core network probes
      (internet/ping/tcp/http/dns + the tcp/host_awake/gateway inline
      dispatch). ~340 LOC.
    - `_probes_integrations.py` — the 14 sensor-backed probes
      (roku/HA×2/weather/iCal/power/solar/SNMP×3/media/webhook/MQTT/
      EPG) + their helpers + token constants. ~983 LOC.
- **Architectural decisions:**
  - **Slice by the axis the domain varies along.** `external_sensors`
    was sliced by *ingestion shape* (`_crud` / `_pollers` / `_inbound`
    / `_query`), not the `devices/`-style read/write split — because
    poll vs. webhook vs. subscriber is the real axis of variation.
    The subpackage convention explicitly allows whatever cohesive
    slices a domain needs; documented in `architecture.md`
    §"Service subpackages".
  - **A dependency-free `_common.py` leaf is fine.** `_iso` is used by
    all four other slices; a tiny shared leaf is not over-
    fragmentation — it is what stops the slices importing each other.
  - **One-directional module-level import is allowed where no cycle is
    possible.** `_probes.py` imports `_probes_integrations` at module
    level; the latter imports nothing back (its service imports are
    all deferred). Cleaner than a deferred import when the DAG is
    provably acyclic. Documented alongside the deferred-import rule.
  - **`docs/design.md` created** — was missing; now holds the design
    rationale (local-first contract, the three ingestion shapes, the
    modality model) that `architecture.md` (structure) and the RFCs
    (point decisions) did not centralize.
- **Files impacted:**
  - 1 service file deleted, 6 created (the `external_sensors/`
    subpackage)
  - 1 probe file rewritten + 1 created (`_probes_integrations.py`)
  - 0 blueprint/template/scheduler changes — public import paths
    preserved end-to-end
  - 3 docs: `architecture.md` (source-layout tree + subpackage
    convention), `refactor-log.md` (this entry), `design.md` (new)
- **Risks:**
  - Import-path breakage was the main risk; mitigated by re-exporting
    every externally-referenced symbol at each `__init__.py` and
    verified with a full `create_app()` smoke test in the built image
    (8 blueprints register; all 13 `external_sensors` re-exports +
    `run_probe` + the integration probes resolve).
  - `_probes.py`→`_probes_integrations.py` module-level import: safe
    today (one-directional, acyclic). A future integration probe that
    imports `_probes` at module load would reintroduce a cycle —
    keep integration-probe service imports deferred.
- **Remaining technical debt / not done:**
  - `services/device_power.py` (723 LOC) — cohesive single-domain;
    splittable along serialize / query / rollup / fleet-summary if it
    grows further.
  - `services/watchdog.py` (696 LOC), `blueprints/admin/rules.py`
    (645), `settings.py` (596), `devices_ui.py` (563) — over the soft
    limits but only ~1.2–1.4×, far below the two outliers just fixed.
  - The probe-shape reference card in `templates/rules/edit.html`
    documents only ~7 of the ~25 probe kinds — stale; backfill.
  - Underscore-prefixed cross-module helpers (flagged in the
    2026-05-14 entry) still un-promoted.
- **Next recommended targets:**
  1. `blueprints/admin/rules.py` (645 LOC) — split the JSON-API
     handlers from the UI handlers, or extract the probe-shape
     reference into a partial; ~1.3× the limit.
  2. `blueprints/admin/settings.py` (596) + `devices_ui.py` (563) —
     same over-limit class; revisit together.
  3. `services/device_power.py` (723) — split when the P1/P3 power
     work next extends it.

---

## 2026-05-14 — service subpackages: devices + watchdog_runtime

- **Branch:** `main` (single atomic commit)
- **Releases included:** v0.5.15
- **Scope:** moderate refactor — two oversized service files split into
  feature-internal subpackages, plus three small cleanup items.
- **Key changes:**
  - `app/services/devices.py` (700 LOC, 2.8× the documented 250-LOC
    soft limit) → `app/services/devices/` subpackage:
    - `__init__.py` — public API surface; re-exports all external
      symbols (15 functions + 1 class) so `from app.services.devices
      import …` keeps resolving for the 8 importer sites.
    - `_serialize.py` — `serialize_device`, `_heartbeat_state_for`,
      `_derive_central_status`, `_serialize_assignment`, `_iso`.
    - `_query.py` — `find_by_mac`, `latest_stable_release_dict`,
      `firmware_version_breakdown`, `list_devices`,
      `get_device_detail`, `_latest_heartbeat_by_device`,
      `_active_assignments_by_device`.
    - `_mutations.py` — `update_device`, `delete_device`,
      `delete_devices_bulk`, `enqueue_display_name_sync`,
      `UnknownPatchFieldError`, `_PATCHABLE`.
  - `app/services/watchdog_runtime.py` (578 LOC) →
    `app/services/watchdog_runtime/` subpackage:
    - `__init__.py` — `tick()` entrypoint + re-exports for the 3
      cross-module importers (`services/watchdog.py`,
      `services/schedule_runtime.py`, `jobs/scheduler.py`).
    - `_probes.py` — `_run_probe` dispatcher + `_probe_internet`,
      `_probe_ping`, `_probe_tcp`, `_probe_http`, `_probe_dns`.
    - `_state.py` — `_rule_is_due`, `_in_maintenance_window`,
      `_record_event`, `_update_state_and_maybe_fire`.
    - `_actions.py` — `_fire_action`, `_fire_cycle`, `_fire_hold_off`,
      `_resolve_target_devices`.
  - Cleanup (Phase 1):
    - Deleted empty `app/services/power_samples.py` (0 bytes; the
      actual `ingest_power_samples` lives in `services/events.py`).
    - Added `backups/` to `.gitignore` (was untracked but flagged
      by reconnaissance — SQL dumps + ad-hoc backup folders should
      never enter git history).
    - Fixed `tests/qa/test_v0514_*.py` SQLite incompatibility by
      switching `DeviceHeartbeat.id` to
      `BigInteger().with_variant(Integer(), 'sqlite')`. Postgres
      production behaviour unchanged; SQLite test path now picks up
      the ROWID-alias autoincrement instead of refusing the NULL
      insert.
- **Architectural decisions:**
  - **Subpackage convention codified.** When a service crosses ~2×
    its 250-LOC soft limit *and* the responsibilities inside it are
    separable, split it into `services/<x>/{_serialize, _query,
    _mutations}.py` with `__init__.py` re-exports. Documented in
    `architecture.md` §"Service subpackages" + `contributing.md`
    §"Sizing".
  - **Re-export everything externally referenced** at the package
    root (`__init__.py`), including the underscore-prefixed helpers
    that other modules legitimately need (`_run_probe`,
    `_resolve_target_devices`, etc.). This preserves import paths
    without code-base-wide find-and-replace. Internal-to-package
    files keep the underscore prefix as a "import via root" signal.
  - **Deferred imports inside function bodies** are the canonical
    way to break cycles between split modules. Established pattern
    in this codebase (`services/commands.enqueue_for_device` was
    already deferred from `services/devices`); reused for
    `_state._update_state_and_maybe_fire` ↔
    `_actions._fire_action`.
  - **Out-of-scope deliberately**: `services/firmware.py` (504 LOC,
    cohesive single-domain), `services/inbox.py` (455 LOC, only 3
    top-level functions), `services/announcements.py` (393 LOC,
    borderline), `tests/qa/` feature-mirror restructure
    (`refactor-log` entry below already defers this), `app/schemas/`
    Pydantic dir (still gated on ≥3 endpoints feeling validation
    pain).
- **Files impacted:**
  - 2 files deleted (the old big service modules + 1 empty stub)
  - 8 files created (4 subpackage files × 2 subpackages)
  - 3 docs updated: `architecture.md`, `contributing.md`,
    `refactor-log.md` (this file)
  - 1 model fix: `app/models/devices.py` (DeviceHeartbeat.id variant)
  - 1 ignore-list addition: `.gitignore`
  - 0 blueprint/template changes — public import paths preserved
    end-to-end
- **Risks:**
  - Import-path breakage was the main risk; mitigated by re-exporting
    every externally-referenced symbol and smoke-testing via a full
    `create_app()` factory invocation inside the production image
    (`create_app OK, blueprints: [version, auth, device_api,
    firmware_public, admin_api, admin_ui]`) before deploy.
  - Cross-module use of underscore-prefixed helpers (`_run_probe`,
    `_record_event`, `_resolve_target_devices`) is a smell — they
    have a wider audience than the underscore implies. Promotion to
    public names (drop the underscore) is queued as a future
    refactor target.
  - The deferred-import cycle-break in `_state.py` works but is
    sensitive: a future caller that imports `_actions` at module
    load-time before `_state` will not see the issue, while the
    reverse order would. Documented in `_state.py` docstring.
- **Remaining technical debt:**
  - `services/firmware.py` (504 LOC) — defer until growth or scan-
    path complexity demands it.
  - `services/inbox.py` (455 LOC) — large because of inline logic
    in 3 functions; defer; revisit if a 4th top-level entry appears.
  - `services/announcements.py` (393 LOC) — borderline; revisit
    after B20 follow-on work lands.
  - Underscore-prefixed cross-module imports
    (`_run_probe`, `_record_event`, `_resolve_target_devices`, etc.):
    promote to public names in a future "naming cleanup" refactor.
  - `tests/qa/` flat layout: still defer until ≥150 tests.
  - `app/schemas/` empty dir: still defer pending Pydantic
    decision.
- **Next recommended targets:**
  1. Promote the underscore-prefixed cross-module helpers to public
     names (`run_probe`, `record_event`, `resolve_target_devices`,
     `derive_central_status`, etc.). Two-step: add new names as
     aliases first, then delete the underscore versions after
     callers migrate. Low-risk; mostly mechanical.
  2. Split `services/firmware.py` when it next grows (current
     trigger is the B16 power-monitoring track adding a sibling
     ingest-and-rollup module).
  3. Mirror tests under `tests/qa/{admin,device}/` once the suite
     crosses 150 files — currently ~50.

---

## 2026-05-09 — admin-blueprint split + first architecture docs

- **Branch:** `refactor/admin-blueprint-split`
- **Releases included:** v0.2.6 (the split itself)
- **Key changes:**
  - Created `app/blueprints/admin/` subpackage. Each admin feature
    (devices, groups, sites, firmware, users, invitations, audit,
    enrollment-tokens, unregistered, events, dashboard, profile,
    auth-ui, public-invite) is now one ~50–150 line module that
    contains both the UI handlers and the JSON API handlers for that
    feature.
  - Deleted `app/blueprints/admin_ui.py` (945 lines) and
    `app/blueprints/admin_api.py` (784 lines). Endpoint URLs and
    view-function names preserved exactly so `url_for(...)` calls in
    templates continue to resolve.
  - First-ever `docs/architecture.md`, `docs/contributing.md`, and
    `docs/refactor-log.md` (this file) created.
  - `docs/SESSION-LOG-*.md` archived under `docs/sessions/` to keep
    the top-level docs/ readable.
- **Architectural decisions:**
  - Co-locate UI + API per feature: anti-fragmentation rule
    (architecture.md §"Module-boundary principles"). Splitting by
    HTTP-surface alone (the prior layout) split each feature across
    two files and forced a 2-file dance for every change.
  - Two `Blueprint` objects (`admin_api_bp`, `admin_ui_bp`) defined
    in `admin/__init__.py`; submodules import them and decorate. This
    keeps registration order trivial and avoids a per-feature blueprint
    explosion.
  - No new dependency added (Pydantic deferred — see `architecture.md`
    §"Out-of-scope today").
- **Files impacted:**
  - 14 new files under `app/blueprints/admin/`
  - 2 files deleted (the old big blueprints)
  - 1 file modified (`app/__init__.py` — single import-line change)
  - 3 new docs (architecture, contributing, refactor-log)
  - Old session logs moved under `docs/sessions/`
- **Risks:**
  - URL preservation must be verified end-to-end. QA suite + live
    Playwright walk are the gates.
  - Submodule registration order: any module that decorates against
    `admin_api_bp` / `admin_ui_bp` must be imported by
    `admin/__init__.py` before the blueprints are registered with the
    app. Mitigated by importing all submodules from `admin/__init__.py`
    at the bottom of the file.
- **Remaining issues:**
  - `tests/qa/` is still a flat layout. With the new admin/ tree it
    would benefit from a `tests/qa/admin/<feature>_test.py` mirror.
    Deferred — suite is small enough today.
  - Open hardening items in `bug-log.md` (BUG-005 logout revocation,
    BUG-006 v2 rate-limit, etc.) are unaffected by this refactor.
- **Next recommended targets:**
  1. Pydantic schemas in `app/schemas/` to replace ad-hoc
     `request.get_json(silent=True) or {}` access patterns. Wait
     until ≥3 endpoints feel validation pain to justify the dep.
  2. Service-layer error normalisation — some services raise
     `ValueError`, others raise typed errors (`UserError`, etc.). Pick
     one pattern.
  3. Mirror tests under `tests/qa/admin/` once the suite grows past
     ~150 tests.
