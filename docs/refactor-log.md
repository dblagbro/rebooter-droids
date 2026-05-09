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
