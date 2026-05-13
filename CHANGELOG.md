# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.11] - 2026-05-13

### Fixed — B22: scanned releases handed devices the wrong OTA URL

Firmware-team caught 2026-05-13 PM after live UI-driven OTA test:
device on 0.1.15 received a `/device/firmware` response pointing at
the canonical root URL
`/rebooter/firmware/rebooter-0.1.15-dev-central.bin` and got HTTP
404, because the scanned `.bin` actually lives under the per-channel
subdirectory at `/rebooter/firmware/stable/rebooter-0.1.15-dev-central.bin`.

Root cause in `app/services/firmware.py::discover_on_disk_releases`:
the scan loop only enumerates files under `firmware_dir/<channel>/`,
but `download_url` was being set to `{base}/{filename}` (root path).
The upload path got away with the same shape because it copies the
artifact into both locations — the scan path does no such copy. The
device reads `release.download_url` verbatim, so the field-level
mismatch produced the 404.

Fix:
- `discover_on_disk_releases` now sets `download_url = per_channel_url`
- Mirror-row emission for scanned releases drops the bogus root
  `local` mirror row (would have claimed `status=live`+`verified_sha`
  for a URL that doesn't serve anything). `local_per_channel` +
  `local_channel_pointer` are still emitted as before.
- One-shot data fix: existing `firmware_releases.download_url` rows
  for scanned entries (16 rows pre-fix) rewritten via SQL UPDATE so
  in-flight deployments resolve correctly; matching `local` mirror
  rows deleted. Defensive backup at
  `rebooter-droids-db-PRE-B22-fix-20260513T212822Z.sql.gz`.
- Upload-path behaviour is unchanged — it still writes both
  locations and keeps the `local` root URL as a live mirror.

Tests: `tests/qa/test_v0511_scan_download_url.py` — exercises
scan-then-HEAD against the live deployment + asserts mirror-row
layout no longer claims a root-path `local` mirror for scanned
entries.

Backlog: B22 entry added in `docs/BACKLOG.md` (marked FIXED in
v0.5.11). Long-poll `/device/commands` (previously planned as
v0.5.11 per the firmware-team responsiveness ask) is bumped to
v0.5.12 to make room for this hot-fix.

## [0.5.10] - 2026-05-13

### Changed — Pending-adoption responsiveness (firmware-team priority bump)

Two small responsiveness wins ahead of the bigger v0.5.11 long-poll
work for `/device/commands`:

- `/api/v1/device/announce` now responds to pending devices with
  `retry_after_seconds: 5` (was `30`). Operator clicks "Adopt", the
  device sees the token within ~5s instead of up to 30s. Tunable
  via the new `REBOOTER_ANNOUNCE_PENDING_RETRY_AFTER_SECONDS` env
  (default 5). Other states (rejected/adopted/registered/
  awaiting_register) unchanged.
- `/app/pending-adoption` now auto-refreshes every 3 seconds via a
  small JS interval. The refresh is suppressed when (a) the browser
  tab is backgrounded, (b) an input/textarea/select is focused, or
  (c) a `<dialog open>` is showing — so it never eats operator
  keystrokes during typed-confirm prompts. No SSE/WebSocket; the
  fleet-side request rate stays bounded by operator count, not
  device count.

Files: `app/config.py`, `app/services/announcements.py`,
`templates/pending_adoption.html`.

## [0.5.9] - 2026-05-13

### Added — Multi-target `internet` watchdog probe

Pre-v0.5.9 the `probe.kind=='internet'` watchdog probe was a
single hardcoded `_probe_tcp("1.1.1.1", 53)` — a single upstream
host issue could falsely look like an internet outage and fire a
power-cycle. Firmware/product side asked for the same multi-target
model the device-side internet watchdog already uses.

The `internet` probe now walks a list of TCP targets. Defaults
(applied when the rule does not pin its own list) are:

- `1.1.1.1:53` (Cloudflare)
- `8.8.8.8:53` (Google)
- `4.2.2.2:53` (Level 3)

Semantics: rule outcome is **success** if ANY configured target
responds, **failure** only when ALL fail. Every target is probed
every tick (not short-circuit) so the event log always reports
the complete picture — operators can tell "one resolver blip"
from "real outage" without opening the API.

Event-log `details` payload now carries:

```
{
  "targets_succeeded": [{"host": "1.1.1.1", "port": 53}, ...],
  "targets_failed":    [{"host": "8.8.8.8", "port": 53, "error": "tcp_connect_failed"}, ...],
  "targets_total":     3,
  "used_default_targets": true   // only when defaults were substituted
}
```

UI: when `probe_kind=='internet'`, the create form now shows a
repeatable host/port row widget pre-filled with the three
defaults, with `+ add target` / `remove` buttons (max 8). The
recent-events log row inline-renders `<N>/<total> ok · failed: …`
so the multi-target outcome is visible at a glance.

Validation: `probe.targets` must be a list, length 1-8; each
entry must be `{host: <non-empty str>, port: int 1-65535}`.
Invalid shapes are rejected at create-time with a clear error.

Backward-compatible: every existing internet rule auto-upgrades
on the next tick — no migration, no operator action. The
plain-English rule sentence now reads "outbound internet
connectivity (3 default targets)" or "(N targets)" so the rule
list communicates the new scope.

Files: `app/services/watchdog_runtime.py`,
`app/services/watchdog.py`, `app/blueprints/admin/rules.py`,
`templates/rules/index.html`,
`tests/qa/test_v0509_internet_multitarget.py`.

## [0.5.8] - 2026-05-13

### Added — Auto-push display_name on restore-after-reflash

QA finding 2026-05-13: hub-side B20 restore correctly preserves
the device row identity (display_name "Erica's Subwoofer", id
`dev_01KR8127W5XMP6MDF34J0TXQP9`, audit history, group memberships)
but the reflashed device kept its local `device_name="Rebooter"`
because hub-side restore didn't push hub-side metadata back DOWN
to the device.

Short-term fix per firmware-team collaboration:

In `consume_enrollment_token`'s restore branch, after the
credential rotation completes, automatically enqueue an
`apply_config` command carrying the hub's `display_name` as
`device_name`. Delivered on the device's first `/device/commands`
poll after `/register` completes — typically ~30 seconds.

Best-effort; never raises out of `/register`. If the enqueue
fails (e.g., command-queue full, unusual error) the restore
itself still succeeds — only the auto-push is skipped, and the
operator can manually re-enqueue via /app/devices later.

Audit event: `device.restore_config_pushed` with
`{trigger: "restore_after_reflash", pushed_fields: [...], device_name: ...}`.

### Medium-term tracked separately as B21

Full `desired_config` blob on each device row (matches the locked
v0.1 apply_config schema), `last_reported_config` for drift
detection, operator-edit UI on device detail, optional auto-
repair-on-drift. Will land as v0.6.0 behind a feature flag.

## [0.5.7] - 2026-05-12

### Added — B20: MAC-based duplicate detection at adoption + restore-vs-fresh choice

Operator hit a duplicate-device bug 2026-05-12 PM when reflashing
Erica's Subwoofer (.30): same physical hardware, two device_ids.
Orphan audit history, group memberships, scheduled rules on the
old row. Same problem will hit when the other 4 bricked speakers
get reflashed.

Schema:
- `enrollment_tokens.target_device_id` — VARCHAR(40) NULL FK → devices(id)
  ON DELETE SET NULL. When set, `/device/register` REBINDS the
  existing device row instead of creating a new one. Idempotent
  `ADD COLUMN IF NOT EXISTS` step in `bootstrap._ensure_columns`.

New `registration_state`: `decommissioned` — set by the
"Decommission + adopt fresh" flow on the dupe-MAC card. Hidden
from `find_by_mac` so future reflashes don't surface abandoned
rows.

Service layer:
- `app/services/devices.py::find_by_mac(mac)` — case-insensitive
  MAC lookup, excludes decommissioned rows.
- `app/services/enrollment.py::mint_enrollment_token(..., target_device_id=)` — pass-through to new column.
- `app/services/enrollment.py::consume_enrollment_token` — branches
  on `target_device_id`. Restore path: verifies MAC match
  defensively, updates existing row in place (firmware_version,
  local_ip, registration_state='active', last_heartbeat_at=NULL),
  rotates `device_credentials`, returns EXISTING `device_id`.
  Fresh path unchanged.
- `app/services/announcements.py::adopt(..., mode=, target_device_id=)` —
  new `mode='restore'` parameter; verifies target exists +
  MAC-matches; mints enrollment token with `target_device_id` set.

UI:
- `/app/pending-adoption` page now passes `existing_devices` per
  announcement via `find_by_mac`.
- Template: when a MAC dupe exists, renders amber-bordered dupe-
  warning card with each existing device's display_name + id +
  firmware + prior IP + last-heartbeat-age. Three actions per
  matched device:
  - **✓ Restore to this device** — visually dominant green button.
    Default for stale/offline prior rows.
  - **Decommission + adopt fresh** — secondary; double-confirm
    requires typing "decommission". Marks old row decommissioned
    (preserved for audit), then standard fresh adopt.
  - **or adopt as new (anyway)** — de-emphasised link-style;
    double-confirm requires typing "duplicate". Creates a second
    logical device for the same physical hardware. Should be rare.
- No-dupe case: pending-adoption UI unchanged.

New routes:
- `POST /app/pending-adoption/<ann_id>/restore/<existing_device_id>`
- `POST /app/pending-adoption/<ann_id>/decommission-and-adopt/<existing_device_id>`

Audit events:
- `device.restored_from_reflash` — restore path
- `device.decommissioned_for_replacement` — decommission-and-adopt
- `device.adopted_with_mac_duplicate` — fresh adopt when dupe existed

Back-compat: fresh adoption when no dupe = identical behaviour to
v0.5.6. All new code paths gated on operator-chosen action.

### Why now

The 4 bricked Erica's speakers (R.L., F.L., F.R., R.R is healthy,
Subwoofer already partially-dupe'd) will all hit this exact flow
when physically reflashed. Without B20 each one creates an orphan
row and split audit history. With B20 the operator picks "Restore"
on each one's pending-adoption card and identity is preserved
across the reflash.

## [0.5.6] - 2026-05-12

### Added — LAN-bridge command types for remote fleet recovery

Three new entries in `commands.ALLOWED_TYPES` so the hub can enqueue
the LAN-recovery commands the firmware team added in v0.1.11:

- **`lan_scan`** — payload `{start, end}` (integer last-octet
  range, 1-254 each, max 254 IPs). Tells a bridge device to scan
  its LAN subnet for live rebooter devices and return the map via
  `/device/command-result`.

- **`lan_proxy`** — payload `{ip, path, method, body?, headers?}`.
  Tells a bridge device to make an HTTP request to a LAN peer
  (e.g. `POST http://192.168.1.30/api/system/reboot`) and return
  the response. `method` ∈ {GET, POST}; `path` must start with `/`.

- **`lan_ota_push`** — payload `{ip, url, sha256?}`. Tells a bridge
  device to instruct a LAN peer to OTA-pull from the given URL and
  self-flash. Unlocks the operator-remote silent-fleet recovery
  path documented in B19/staged-deployments without requiring the
  operator to be on the LAN.

Validation is light by design — these are operator-triggered
recovery commands, not customer-facing endpoints, so the schema
checks catch the obvious wrong-type cases (`ip` is a string,
`url` starts with `http://` or `https://`, etc.) but don't try to
prevent every misuse.

#### Required firmware

Device must be on **0.1.11-dev-central or later** to dispatch these
command types. Older firmware silently ignored or rejected them.

#### Operational pattern

The hub-side recovery sequence remains operator-paused per the
fail-safe gate, but with 0.1.11 + v0.5.6 the actual mechanics are:

1. Operator green-lights an OTA push to a bridge device (e.g. R.R.
   Speaker) — un-pauses the paused `deployment_assignment`.
2. Bridge device upgrades to 0.1.11.
3. Operator enqueues `lan_scan` against the bridge device — finds
   the silent peers' IPs.
4. Operator enqueues `lan_ota_push` against the bridge device,
   targeting each silent peer with the same firmware URL.
5. Silent peers self-flash, reboot, and re-authenticate with their
   restored hub-side credentials. Fleet recovered.

No hub-side test ships yet — coverage will land with the operator-
fired recovery run. The schema-validator code path is exercised
implicitly by every `lan_*` command issued.

## [0.5.5] - 2026-05-11

### Refactor — split `devices.py` blueprint by concern

The heaviest blueprint in the codebase (630 lines, mixed UI + API
+ bulk-delete + upgrade-to-latest + protection toggle + cancel
command + ...) split into three files by concern:

```
app/blueprints/admin/devices.py       —  23 lines (back-compat shim)
app/blueprints/admin/devices_ui.py    — 427 lines (UI handlers)
app/blueprints/admin/devices_api.py   — 246 lines (JSON API handlers)
```

#### What's where now

- **`devices_ui.py`** — every `@admin_ui_bp` handler:
  list/detail/update/delete/send_command/cancel_command/
  upgrade-to-latest/bulk-delete (UI)/protection-toggle.
  Includes the `_show_qa_fixtures` helper.
- **`devices_api.py`** — every `@admin_api_bp` handler:
  list/get/patch/send_command/delete/bulk-delete (API)/
  cancel_command.
- **`devices.py`** — thin shim that imports both for side-effect
  route registration. Preserves `from app.blueprints.admin.devices
  import ...` for any external introspection.

#### Endpoint names preserved

All `url_for("admin_ui.<name>")` and `url_for("admin_api.<name>")`
calls in templates and tests resolve unchanged. The blueprint
object (`admin_ui_bp` / `admin_api_bp`) is shared across files —
both new modules import it from `app.blueprints.admin`.

#### Why now

- v0.5.5 unblocks the upcoming B18 ship (inline on/off toggle in
  the devices list) — that work would have added another ~80
  lines of UI logic to the already-630-line single file.
- Lines-per-file rule (operator's coordinator-hub convention was
  <1,200 per file) was still satisfied at 630 but trending
  upward. Splitting now is cheap; splitting at 900+ is harder.

No behaviour change. No new tests needed beyond the existing
regression coverage (24+ live tests already exercise the routes,
all green post-split).

## [0.5.4] - 2026-05-11

### Refactor — version helpers extracted; package import-clean

Two related, behaviour-preserving changes:

- `is_upgrade()` and `_version_sort_key()` moved from
  `app/services/devices.py` to a new minimal module
  `app/services/_versions.py`. Pure-Python, no Flask/SQLAlchemy
  dependencies. Re-exported from `app/services/devices.py` for
  back-compat with existing callers (template Jinja global
  `is_upgrade=`, blueprint imports).

- `app/__init__.py` deferred its top-level imports of
  `app.middleware.rate_limit.init_rate_limit` and
  `app.middleware.response.register_envelope_handlers` into the
  `create_app()` function. The package can now be imported
  (e.g. for unit tests) without pulling in the entire Flask
  runtime stack (`flask_limiter`, etc.). The Flask app itself
  still loads them on `create_app()` so live behaviour is
  unchanged.

#### Why

`tests/qa/test_v0429_upgrade_direction.py` was failing on developer
hosts that don't have `flask_limiter` installed (e.g. anywhere
outside the Docker image) because importing
`from app.services.devices import is_upgrade` triggered
`app/__init__.py`'s eager Flask-stack imports. Now the test file
imports from `app.services._versions` directly and the package
load is clean.

4/4 v0.4.29 tests pass on the host now (previously: 4/4 fail
outside the container).

### Tests

- v0.4.29 upgrade-direction tests now run host-side without the
  container. Test file's `is_upgrade` import points at
  `app.services._versions`.

## [0.5.3] - 2026-05-11

### Fixed — clicking Upgrade button could delete the device (critical)

Operator hit this twice today. Audit-log evidence:

- 2026-05-11 17:45:50 UTC — clicked Upgrade on R.L. Speaker;
  audit emitted `device.bulk_deleted_per_device` for R.L. instead of
  `device.upgrade_initiated`.
- 2026-05-11 01:53 UTC — same pattern deleted 4 devices.

**Root cause**: per-row upgrade `<form>` tags were rendered INSIDE
the wrapping bulk-delete `<form>`. Per the WHATWG HTML5 parser
spec:

- An inner `<form>` start tag inside an existing form context is a
  parse error and is **ignored** (no nested form element created).
- The corresponding `</form>` end tag DOES close whichever form is
  currently open — which means it closes the OUTER bulk-delete
  form mid-table.
- Buttons inside the "ignored" inner form become submitters of the
  OUTER form. Clicking Upgrade submitted the bulk-delete form with
  whatever device_id checkboxes were checked, then showed the
  `confirm()` prompt from `bulk_select.js` that the operator
  misread as the upgrade confirmation.

#### Fix

- Moved the bulk-delete `<form>` to AFTER the table + mobile cards.
  It now only wraps the bulk-action bar at the bottom.
- Row checkboxes (desktop + mobile + master) carry the HTML5
  `form="devices-bulk-delete-form"` attribute to associate with the
  form across the DOM. No nesting.
- Per-row upgrade forms are now top-level (no enclosing form).
- `bulk_select.js` switched from `form.querySelectorAll()`
  (descendant-only) to a document-wide query filtered by `.form`
  ownership so it still picks up the now-DOM-detached checkboxes.

#### Recovery

Erica's R.L. Speaker device row + credentials restored from the
v0.5.2 POST-RESTORE backup at 2026-05-11 17:50 UTC via targeted
INSERT replay (same procedure used for the 4 devices on 2026-05-11
02:30).

### Tests

- `tests/qa/test_v0503_devices_list_nested_form.py` — verifies the
  rendered page has no nested forms (max depth ≤ 1, balanced), all
  device_id checkboxes carry the form= attribute, and the bulk-delete
  form has the expected id.

## [0.5.2] - 2026-05-11

### Fixed — misleading "1 device · Pending adoption →" sub-header on /app/devices

Operator-flagged regression: the devices-list sub-line rendered

```
{{ devices|length }} device · Pending adoption →
```

which, when the fleet view contained 1 device, read as
"1 device · Pending adoption →" — the eye parses this as
**"1 device pending adoption →"**. The "1" was actually the
fleet-count and the link target was an unrelated page showing
zero pending announcements. Clicking through left the operator
staring at "No pending devices" wondering where the alleged 1
went.

#### Fix (three layers)

- **New `count_pending_announcements()` service helper** in
  `app/services/announcements.py` — single `SELECT COUNT(*)` over
  `device_announcements` filtered to rows where `consumed_at IS
  NULL AND rejected_at IS NULL` (the same predicate
  `list_announcements()` uses by default).
- **Wired into the devices-list page context** as
  `pending_adoption_count`.
- **Sub-header restructured**:
  - Fleet count now ends with the qualifier "in fleet" so it
    cannot be misread as "pending": "5 devices in fleet".
  - Pending-adoption link is on its own line as a styled chip
    with the count baked into the visible text: "Pending
    adoption: 0 →" or "Pending adoption: N →".
  - Chip turns **amber** when the count is > 0 so the operator
    actually notices when there's something to action.

### Tests

- `tests/qa/test_v0502_pending_adoption_count.py` — verifies the
  link-bound count format and cross-checks against the actual
  pending-adoption page contents.

## [0.5.1] - 2026-05-11

### Fixed — v0.5.0 backfill over-granted bindings to operator users

v0.5.0's one-shot RBAC backfill gated on `users.is_admin` which is
also True for users with `role='operator'` in this schema. Result:
every active operator got incorrect site-admin bindings in
`role_bindings` that they should not have per B10 Q2 ("operators
→ no rows; forced re-grant").

Live evidence post-v0.5.0 deploy:

```
scope_type | role        | count
-----------+-------------+-------
global     | super_admin |     1   ← correct
site       | admin       |   220   ← expected 110 (22 admins × 5 sites);
                                     extra 110 came from 22 operators
                                     × 5 sites being mis-classified
```

#### Fix

`ensure_role_bindings_backfill()` now gates on the actual `role`
column instead of `is_admin`:

- `is_super_admin=True` → `('global', NULL, 'super_admin')`
- `role == 'admin'` (and not super) → one row per site
- everything else (including operators, viewers) → **no rows**

#### Corrective one-shot

A new corrective step runs once on first deploy of v0.5.1,
tracked via `rbac.role_bindings_v050_correction_applied_at`. It:

1. Deletes every `role_bindings` row whose user has
   `role IN ('operator', 'viewer')` — drops the bad rows v0.5.0
   created.
2. De-duplicates `(user_id, scope_type, scope_id)` rows in case
   gunicorn-worker contention in v0.5.0 also produced duplicates
   (using `IS NOT DISTINCT FROM` so NULL scope_ids dedupe correctly).
3. Records completion in `runtime_settings`. Never re-runs.

The v0.5.0 backfill tracking row (`rbac.role_bindings_backfilled_at`)
is **preserved** by the correction step — we don't want the
corrected backfill to also re-run on every restart. The correction
is one-shot, the backfill remains one-shot, both are idempotent.

If the v0.5.0 image somehow gets re-deployed before v0.5.1 lands
(e.g., a rollback) the correction would re-execute on the next
v0.5.1 upgrade, which is the intended safety net.

## [0.5.0] - 2026-05-10

### Added — RBAC role_bindings table + one-shot backfill (Tier A / A1)

Foundation ship of the RBAC scoping migration locked by B10
redlines 2026-05-10 PM (RFC-003 §9.0). **Non-enforcing.** This
release adds the table, populates it from the legacy
`users.is_super_admin` / `is_admin` / `role` columns, and exposes
a service-level CRUD + effective-scope resolver. The shadow-mode
middleware that *logs* would-have-denied decisions (A2) and the
enforce flip (A8) are later ships gated on ≥ 7 days of clean
shadow-log soak per RFC-003 §6.3.

#### New schema

```
role_bindings
├── id                  (rb_<ulid>)
├── user_id             FK users.id ON DELETE CASCADE
├── scope_type          'global' | 'site' | 'group' | 'device'
├── scope_id            NULL for global; ULID otherwise
├── role                'super_admin' | 'admin' | 'operator' | 'viewer'
├── created_at, updated_at
├── created_by_user_id  FK users.id ON DELETE SET NULL
└── UNIQUE (user_id, scope_type, scope_id)
INDEX (user_id), (scope_type, scope_id)
```

#### Auto-backfill (one-shot per database, idempotent)

Runs on container startup after `ensure_schema()` /
`ensure_bootstrap_admin()`. Tracked via a `runtime_settings` row
under `rbac.role_bindings_backfilled_at` so it's a hard no-op on
subsequent boots. Per B10 Q2:

- existing super_admins → `('global', NULL, 'super_admin')`
- existing admins (not super) → one row per current `site_id`,
  `('site', <site_id>, 'admin')`. If no sites exist yet, one
  `('global', NULL, 'admin')` row as a safety net so the operator
  isn't locked out on day one.
- existing operators / viewers → **no rows**. Per B10 Q2, the
  operator tier must be re-granted explicitly by an admin before
  the enforce flip.

If the backfill errors (e.g., DB constraint surprise), startup
continues — we never block a healthy container on this one-shot
data migration. The exception is logged; operator can re-run by
deleting the `runtime_settings` tracking row.

#### New service module

`app/services/role_bindings.py`:

- `grant(user_id, scope_type, scope_id, role)` — idempotent upsert
- `revoke(user_id, scope_type, scope_id)` — drop a binding
- `list_for_user(user_id)` — enumerate
- `has_global_role(user_id, role_needed)` — fast hot-path check
- `effective_site_ids(user_id, role_needed)` → `"ALL"` sentinel or set
- `effective_device_ids(user_id, role_needed)` → `"ALL"` or set,
  computed by unioning global / site / group / device bindings via
  GroupMembership joins
- `can_act_on_device(user_id, device_id, role_needed)`
- `can_act_on_site(user_id, site_id, role_needed)`

Role-hierarchy enforcement built in: a `super_admin` binding
satisfies an `admin`-required check; an `admin` binding satisfies
`operator`; etc.

#### What this doesn't do (yet)

- Does **not** change any existing auth-decorator behaviour. All
  `@admin_required_ui` / `@role_required_*` decorators keep their
  v0.4.x semantics. The legacy `users.role` + `users.is_admin` +
  `users.is_super_admin` columns stay authoritative until A8.
- Does **not** scope queries. `GET /api/v1/admin/devices` still
  returns every device an admin can see today — scope-filtered
  queries land in A4.
- Does **not** expose any UI for grant/revoke. CRUD UI lands in
  A6 / A7.

### Tests

- `tests/qa/test_v0500_role_bindings.py` — verifies v0.5.0
  deployment health, legacy auth back-compat preserved.

## [0.4.34] - 2026-05-10

### Fixed — Firmware on-disk scan misses recently-written .bin files

Firmware team reported (2026-05-10 PM) that
`POST /api/v1/admin/firmware/scan` failed to register a
freshly-SCP'd `rebooter-0.1.6-dev-central.bin` even though the file
was on the host filesystem before the scan ran. The next invocation
~2 minutes later picked it up cleanly — classic bind-mount
cache-miss pattern between the host's
`/mnt/s/code/rebooter-droids/data/firmware/stable/` and the
container's `/data/firmware/stable/`.

Fix: call `os.sync()` at the start of `discover_on_disk_releases`
so any pending writes are flushed before the directory walk. Cost
is one syscall per scan, executed only when the operator triggers
the scan — negligible. Best-effort; falls through cleanly on
platforms that don't support it.

Operationally there is no behavioural change for the working
case; the buggy case (recently-written file invisible to
container's `iterdir`) now returns the correct discovered set on
the first try.

## [0.4.33] - 2026-05-10

### Changed — Firmware UI moves under Settings (D3)

The firmware-releases + deployments page that's been at
`/app/firmware` since v0.1 is now canonically a Settings tab at
`/app/settings/firmware`. Matches how the Settings tab strip
already named it. The legacy URL keeps working as a **302 redirect**
to the new canonical URL so existing bookmarks, external docs, the
one-click upgrade button copy, and any operator muscle memory all
keep functioning.

Template gains the Settings tab strip at top + a "Settings" page
header so the breadcrumb mental model matches the URL.

No data migration. No API change. Pure UX consolidation.

### Tests

- `tests/qa/test_v0433_firmware_settings_tab.py` — new URL renders
  with tab strip; legacy URL 302s to the new home.

## [0.4.32] - 2026-05-10

### Added — History export + free-text search (C2 + C3)

Two related history-page features land together because they share
the form area:

- **CSV / JSON export** (`?export=csv` / `?export=json`). Streams
  the current filter view as a download with a sane
  `Content-Disposition` header. Honours every filter currently in
  the URL — source, action_prefix, free-text — so the file is
  "what you see on screen". Caps at 50,000 rows per request;
  narrow filters for more. New `Export CSV` / `Export JSON`
  buttons below the filter form.
- **Free-text search** (`?q=<text>`). ILIKE wildcard match across:
  - actor / actor_user_id
  - action (with substring match, broader than the exact-match
    `?action=` field)
  - target_type / target_id / message
  - `details` JSON cast to text — searches anything inside the
    blob in one box
  Works across all four sources (audit / watchdog_probe /
  device_event / all). Search field renders full-width above the
  existing filter inputs.

### Backlog

- **B16 power-monitoring design doc** drafted at
  `docs/B16-power-analytics-design.md`. 4-tier architecture, 8-ship
  roadmap, full privacy posture, retention strategy, ingestion API
  contract, cross-plug correlation as the "MIT-tier" feature.
  Pending firmware-team confirmation of the metering chip (CSE7766
  on Sonoff S31 per Tasmota / ESPHome; corrected from the original
  HLW8032 assumption).

### Tests

- `tests/qa/test_v0432_history_export_search.py` — CSV/JSON export
  headers + content, action_prefix-honoured export, search-narrowing
  guarantee, search field renders.

## [0.4.31] - 2026-05-10

### Improved — Device enrolment wizard (E5 from continuation plan v2)

`/app/devices/new` shipped in v0.3.1 as the guided one-stop enrolment
page but had three rough edges fixed in v0.4.31:

- **Site selector** in the form when sites exist. The mint service
  already accepted `site_id`; the wizard form just didn't expose it.
- **TTL picker** — operator-friendly options (1 h / 24 h / 7 d / 30 d)
  with 24 h as the default. Picks one of an allow-listed set; invalid
  values silently fall back to the system default rather than 400.
- **Cross-link to `/app/pending-adoption`** for the no-serial-access
  flow. Operators who can't paste a token directly into the device
  now have a clear path to the v0.4.20 announce-then-adopt UX.

The Status page's `+ Enrol a device` button and the
`unregistered_devices.html` "mint one here" link both used to point
at `/app/enrollment-tokens` (the token-list management view). They
now route to `/app/devices/new` (the guided wizard) — a much better
operator experience.

QR-code support stays deferred (no operator mobile workflow yet that
would consume one; pending-adoption already covers the "I can't
paste the token" case).

### Tests

- `tests/qa/test_v0431_enrol_wizard.py` covers TTL picker render +
  custom-TTL submit round-trip + Status-page link-routing.

## [0.4.30] - 2026-05-10

### Added — Unified history feed (C1 from continuation plan v2)

`/app/history` now surfaces three event streams behind a single chip
nav:

- **Audit** (`source=audit`, default) — same `audit_events` view that
  shipped in v0.3.0 / v0.4.27, with chip filters preserved.
- **Watchdog probes** (`source=watchdog_probe`) — outcomes from the
  `watchdog_probe_events` table; each row is rendered as a
  `watchdog_probe.<outcome>` action.
- **Device events** (`source=device_event`) — rows from
  `device_events` posted via `POST /api/v1/device/events`.
- **All sources** (`source=all`) — time-merged union of the three,
  with an extra Source column showing per-row provenance.

Backed by a new `app/services/history.py` that normalises the three
on-disk tables into one shape (`source, at, actor, action, target,
details, ip`). Defaults preserve back-compat — anyone bookmarked at
`/app/history?action_prefix=...` keeps seeing audit events.

Schedule fires and notification sends keep arriving via the audit
slice today; they'll get their own sources when the notification
surface ships (Tier C of `redesign-continuation-plan-v2.md`).

### Tests

- `tests/qa/test_v0430_history_sources.py` — verifies the source
  picker renders, switching flips active state, `watchdog_probe`
  rows actually appear, the new "all" view adds a Source column,
  and the v0.4.27 audit chip filter still narrows correctly.

## [0.4.29] - 2026-05-10

### Fixed — Upgrade button could offer downgrades

`latest_stable_release_dict()` picked "latest" by upload time
(`created_at desc`), which surfaced the most-recently-uploaded
stable release regardless of its version number. When an older
release was re-uploaded after a newer one (e.g. `0.1.2` pushed
after the fleet had already moved to `0.1.5`), the per-device
upgrade button on `/app/devices` offered a **downgrade**.

Fix has three layers:

- **Server-side selection.** `latest_stable_release_dict()` now
  picks the highest-version stable release, comparing by the
  dotted-int numeric prefix of the version string.
- **Template gate.** Replaces the old string `!=` check with a new
  `is_upgrade(target, current)` helper exposed as a Jinja global.
  Only shows the button when the target is strictly newer
  numerically. Same-numeric-prefix (e.g. label-only changes like
  `0.1.1-dev-central` → `0.1.1-dev-central-ui`) is intentionally
  not flagged as an upgrade.
- **Handler guard.** The submit handler refuses to create a
  deployment if the target is not strictly newer than what's on
  the device. Defends against stale pages or directly-posted forms.

Unit tests at `tests/qa/test_v0429_upgrade_direction.py` cover the
comparator (numeric ordering, label-only ties, `None` / empty
inputs, and the actual fleet versions from 2026-05-10).

## [0.4.28] - 2026-05-10

### Fixed

- **One-click Upgrade button regression.** Clicking the per-device
  "Upgrade to latest" button on `/app/devices` returned
  `{"error":{"code":"internal_error"...}}` since v0.4.21. Root cause
  was a stray `current_app.config["SETTINGS"]` reference in
  `device_upgrade_to_latest_submit` that was assigning to an unused
  local; `current_app` was never imported, so the very first click
  hit a `NameError` and bubbled up to the generic 500 handler. Fix
  is to delete the dead line (the `settings` local was never read).
  Regression test added at `tests/qa/test_v0428_upgrade_button.py`.

## [0.4.27] - 2026-05-10

### Added — History page chip filters + API.md refresh

- **History page chips.** `/app/history` now has a chip nav with
  one-click filters for the 14 most common audit-action prefixes
  (`device`, `device_announcement`, `watchdog_rule`, `schedule`,
  `firmware`, `user`, `attention`, `maintenance_mode`,
  `password_reset`, `enrollment_token`, `smtp`, `network`, `system`,
  `group`). Backed by a new `action_prefix` query parameter on
  `GET /admin/audit` that does a `LIKE '<prefix>.%'` match. Free-text
  filters still work alongside chips.
- **`docs/API.md` refreshed.** Documents endpoints added across the
  v0.4.x series: `/device/announce`, `/device/failsafe`,
  `/admin/pending-adoption/*`, `/admin/firmware/scan`,
  `/admin/maintenance`, `/admin/attention/{id}/ack`,
  `/admin/rules/*` (watchdog rules), `/admin/schedules`,
  `/admin/users/*`, `/admin/invitations/*`,
  `/admin/devices/bulk-delete`, `/admin/devices/{id}/commands/{cid}/cancel`,
  runtime-settings save/clear surface. Adds new error codes
  (`forbidden`, `rate_limited`, `announcement_*`, `maintenance_active`).

### Tests

- `tests/qa/test_v0427_history_chips.py` — verifies chip nav renders,
  active state flips on `action_prefix`, and filtered rows only
  contain matching actions.

## [0.4.26] - 2026-05-10

### Added — Runtime-editable Network + System settings

Settings → Network and Settings → System are now editable. Same
DB-backed-with-env-var-fallback pattern as the SMTP work in v0.4.25.

#### Network tab — fields editable

- **Public base URL** (`network.public_base_url`) — env
  `REBOOTER_PUBLIC_BASE_URL`. **Live** (effective immediately).
- **Firmware public base** — env `REBOOTER_FIRMWARE_PUBLIC_BASE`. **Live.**
- **CORS allowed origins** — env `REBOOTER_CORS_ALLOWED_ORIGINS`.
  **Restart required** (Flask-CORS reads once at app init).
- **Rate-limit exempt IPs** — env `REBOOTER_RATE_LIMIT_EXEMPT_IPS`.
  **Live** (read per-request).
- **Cookie domain** — env `REBOOTER_COOKIE_DOMAIN`. **Restart required**.

#### System tab — fields editable, all **Live**

- **Invitation TTL (seconds)** — env `REBOOTER_INVITATION_TTL_SECONDS`.
  Live: `invitations.mint` now reads from `runtime_settings`.
- **Password-reset TTL (seconds)** — env `REBOOTER_PASSWORD_RESET_TTL_SECONDS`.
  Live: `password_resets.request_reset` reads from `runtime_settings`.
- **Enrollment-token default TTL** — env `REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS`.
  Live: `enrollment.mint_enrollment_token` reads from `runtime_settings`.
- **Session idle timeout** — env `REBOOTER_SESSION_IDLE_TIMEOUT_SECONDS`.
  Restart required (Flask session config wired at app init).
- **Portal name** (display only).

#### New service helpers
- `runtime_settings.network_config()` — full live network config dict
- `runtime_settings.system_config()` — full live system config dict
- `runtime_settings.is_live_editable(name)` — per-field UI badge hint
- `NETWORK_KEYS` / `SYSTEM_KEYS` constants for enumeration

#### New audit hooks
- `network.config_updated` / `network.config_cleared`
- `system.config_updated` / `system.config_cleared`

#### Indicators on every field
Per-field "DB override" vs "env-var fallback" indicator (matches
the v0.4.25 SMTP pattern), plus **live** vs **restart required**
badges so the operator knows what takes effect when.

### Compatibility

- All v0.4.25 routes preserved.
- Empty DB on a fresh deployment still picks up env-var defaults.
- The `_exempt_ips()` rate-limit helper now reads
  `runtime_settings` → env-var fallback so the QA host's
  exemption is preserved.

## [0.4.25] - 2026-05-10

### Added — Runtime-editable SMTP credentials

Settings → Notifications is now editable. Operators can rotate
SMTP creds without recreating the container. Each field reads
DB → env-var fallback; clearing a field reverts to env-var.

- New `runtime_settings` table (key/value, JSON-typed).
- New `app/services/runtime_settings.py` with
  `get(name, env_var=, default=)`, `set_(name, value, user_id=)`,
  `delete(name)`, `has_db_value(name)`, `list_keys()`,
  and a typed `smtp_config()` helper used by the email service.
- `app/services/email.py` now reads SMTP via `runtime_settings.smtp_config()`
  rather than the once-at-startup `Settings` dataclass — DB
  rotations take effect immediately on the next email send.
- New UI:
  - **Edit SMTP settings** form on `/app/settings/notifications`
    with per-field "DB override" / "env-var fallback" indicators.
  - **Save** button (audit-logged as `smtp.config_updated` with
    a list of which fields changed) — masked password preserved
    on round-trip via the `********` sentinel value.
  - **Revert to env-var defaults** button (audit-logged as
    `smtp.config_cleared`) — drops every DB override at once.
- Rendered indicator on every field shows whether it's
  currently DB-backed or env-var-fallback so the operator
  knows live state at a glance.

### Tests

`tests/qa/test_v0425_runtime_smtp.py` — page renders
edit form + save/clear round-trip with HELO field (host /
user / password untouched to avoid breaking real SMTP).

### Compatibility

- All v0.4.24 routes preserved.
- New `runtime_settings` table created via
  `Base.metadata.create_all()` at boot.
- Empty DB on a fresh deployment falls through to env-var
  defaults (zero behavior change without explicit operator
  action).
- Existing email-sending callers (invitations, password-reset,
  send-test, future watchdog notifications) unchanged — they
  continue calling `email.send_email()` which now picks up
  live config.

## [0.4.24] - 2026-05-10

### Docs / state checkpoint

State-checkpoint ship after the announce-poll flow validated
end-to-end on lab-69 + firmware team's RFC-005 §9 redlines came
in. Pure docs ship — no code or schema change.

- **B12 closed.** `docs/notes/2026-05-10-from-firmware-team-rfc005-redlines.md`
  preserves the firmware team's full Q1..Q9 reply. RFC-005 §9
  rewritten with the final answers folded in (slot sizes locked
  A=640KiB / B=1MiB / C=1MiB; Q3 success criteria broader than
  just heartbeat; Q4 6 canonical reason strings agreed; AP-mode
  shipped in `bootstrap-0.2.2`; flash-time config = both serial
  + AP-mode; LittleFS JSON not NVS; Python CLI flash tool
  first; hosting in force with publish-integrity discipline).
- **`docs/BACKLOG.md`** — B12 marked CLOSED.
- **`docs/redesign-continuation-plan.md`** — B12 strikethrough
  in the gated-list.
- Hub-side follow-ups recorded for queue: recognise the 6
  canonical Q4 reason strings in failsafe service, add a "verify
  external mirror" button on /app/firmware (publish-integrity
  per Q9), treat `bootstrap-0.2.2` as the recommended baseline
  for new device bring-up.

### Fleet state at this checkpoint

- **5/5 lab devices online** (lab-30 / lab-67 / lab-69 / lab-207
  / lab-225) — including lab-69 brought up via the new
  `/api/v1/device/announce` flow shipped in v0.4.20
- 4 watchdog rules armed (internet-connectivity, hub-self-check,
  hub-www2-self-check, dns-resolver-health)
- 0 open code-fix bugs
- 316 tests passing, 5 expected skips
- Firmware build with announce-loop: `0.1.5-dev-central` (lab-69's
  current); other 4 devices on 0.1.2 / 0.1.3
- `bootstrap-0.2.2` is the firmware-team-recommended baseline
  going forward (includes Wi-Fi AP fallback per Q5)

## [0.4.23] - 2026-05-10

### Docs / UI copy refresh — UI redesign continuation plan

The Settings tabs Overview / System / Network / Authentication
were carrying stale "Coming in P5/P6" placeholder copy from
v0.3.0. Substantial chunks of P5/P6 actually shipped piecemeal
across v0.4.x but the placeholder copy never got updated. This
ship reconciles that.

- **`docs/redesign-continuation-plan.md`** (NEW) — full map of
  what shipped from the original P5/P6 plan vs what's still
  queued, plus a re-prioritised next-4-ships proposal.
- **`templates/settings/index.html`** — Overview points at the
  per-tab "Live now / Queued" sections + cross-references the
  continuation plan doc.
- **`templates/settings/system.html`** — replaced "Coming in P5"
  empty-state with explicit Live-now (maintenance toggle, schedules)
  + env-var-driven (read-only) sections.
- **`templates/settings/network.html`** — replaced "Coming in P6"
  empty-state with Live-now (CORS, dual hosting, security headers,
  rate-limit exemption) + Editable-UI-queued sections.
- **`templates/settings/auth.html`** — replaced "Coming in P5"
  empty-state with Live-now (session-revoke enforced, password-
  reset, bootstrap admin password persistence, login rate limit,
  invitations) + Queued (RBAC, TOTP, OIDC) sections.

### Compatibility

- All v0.4.22 routes preserved.
- Pure copy / docs change — no code, no schema, no behavior shift.

## [0.4.22] - 2026-05-10

### Security — D / BUG-049: CSP `'unsafe-inline'` dropped from `script-src`

- **CSP `script-src 'self'`** — no longer allows arbitrary inline
  scripts or event handlers. Real defense-in-depth XSS hardening.
- The 1 inline `<script>` previously in `templates/layout.html`
  (theme-flash mitigation) extracted to
  `static/js/theme_flash.js`.
- 18 inline `onsubmit="return confirm(...)"` /
  `onclick="return confirm(...)"` handlers across 12 templates
  migrated to `data-confirm-message="..."` data attributes;
  centrally wired by `static/js/confirm_handlers.js` via
  `addEventListener` on DOMContentLoaded.
- Custom-function inline handlers (`confirmMassAction(this, ...)`,
  `confirmFirmwareDeploy(this)`) replaced with
  `data-mass-action-verb` / `data-mass-action-count` /
  `data-firmware-deploy-confirm` data attributes; wired in
  `static/js/mass_action.js`.
- Hold-off type-the-name confirm preserved via
  `data-confirm-typed-name="..."` (single template, single
  pattern).
- `style-src` keeps `'unsafe-inline'` for now — 123 inline
  `style=` attributes across templates are a separate migration
  with much lower security impact.

### Added — E / Tier-2: Status-inbox attention ack / snooze

- New `attention_acks` table + service. Per-attention-item ack
  with optional snooze duration.
- Status-page attention items now render **Snooze 1h**,
  **Snooze 24h**, and **Ack** buttons (super-admin / admin only).
  Ack hides the item until manually cleared OR the underlying
  state changes (e.g. device comes back online).
- Inbox service filters acked items at read time.
- API: `POST /api/v1/admin/attention/<id>/ack` (with optional
  JSON body `{snooze_seconds, reason}`) +
  `DELETE /api/v1/admin/attention/<id>/ack` (un-ack).
- Audit hooks: `attention.acked`, `attention.unacked`.

### Tests

`tests/qa/test_v0422_csp_and_ack.py` — 6 tests covering CSP
header tightness, no-inline-script-blocks-in-rendered-HTML,
unauth pages still load post-CSP, ack lifecycle, ack hides
items, garbage snooze handled.

### Compatibility

- All v0.4.21 routes preserved.
- New tables created via `Base.metadata.create_all()` at boot.
- The CSP change is breaking for any operator who had heavily
  customised templates with their own inline scripts (none in
  the standard tree).

## [0.4.21] - 2026-05-10

### Added — One-click "Upgrade to <latest-stable>" on the devices list

- **Per-row upgrade button** on `/app/devices`. Whenever a device's
  `firmware_version` differs from the current latest-stable
  release tracked in `firmware_releases`, the row gets a small
  ⬆ button labelled with the target version. One click queues a
  single-device deployment of the latest stable release.
- Equivalent to going to `/app/firmware`, picking the release,
  selecting `target_type=device`, typing the device id — folded
  into one click.
- Confirmation prompt names both the source and target version
  before queueing.
- Hidden when:
  - no stable release is tracked (UI gives operator nothing to
    point at)
  - the device is already on the latest version
  - device opts out of central (`central_management_enabled=false`)
  - the viewer lacks edit permission (super_admin / admin)
- New audit hook `device.upgrade_initiated` with details
  `{via, release_id, release_version, deployment_id}`.
- New endpoint `POST /app/devices/<device_id>/upgrade-to-latest`.
- New service helper `latest_stable_release_dict()` for templates.

### Compatibility

- All v0.4.20 routes preserved.
- The new button is purely additive — the existing
  `/app/firmware` deploy form continues to work.

## [0.4.20] - 2026-05-10

### Added — Pending-adoption flow (operator-driven device onboarding)

Replaces the old "mint a token in the UI, paste into firmware
build at flash time" workflow. Devices flash generic, announce
themselves, get adopted by name.

- **`POST /api/v1/device/announce`** — new unauthenticated endpoint.
  Devices without an enrolment token POST their MAC + claims here
  every ~30s. Hub upserts a `device_announcements` row keyed on
  MAC. Response tells the device to keep polling (`pending`),
  pick up its delivered token (`adopted`, one-shot), wait for
  /register (`awaiting_register`), or back off (`rejected`).
- **New `device_announcements` table** with full lifecycle:
  pending → awaiting_pickup → awaiting_register → registered.
  MAC is the unique key. `adoption_token_secret` is plaintext-
  but-cleared-after-delivery; never exposed to admin queries.
- **`/app/pending-adoption`** admin UI page lists pending devices
  with all claimed metadata + source IP + announce count.
  **Adopt** button mints a 7-day enrolment token and stashes it
  on the row; **Reject** button sets a 1-hour back-off; **Delete**
  cleans up consumed/rejected rows.
- **API:** `GET /api/v1/admin/pending-adoption`,
  `POST /api/v1/admin/pending-adoption/<id>/adopt`,
  `POST /api/v1/admin/pending-adoption/<id>/reject`.
- **Cross-linked** from `consume_enrollment_token` — when a
  device successfully registers via an adopt-delivered token, the
  announcement's `consumed_at` is stamped (best-effort, never
  raises out of /register).
- **Audit hooks:** `device_announcement.adopted`,
  `device_announcement.rejected`, `device_announcement.deleted`.
- **Devices page link** to /app/pending-adoption in the page header.

### Firmware-team contract

Documented at
`docs/notes/2026-05-10-firmware-team-announce-adopt-contract.md`
— full request/response shapes, lifecycle state machine,
recommended timing, idempotency notes. Existing
register-with-baked-in-token flow continues to work; this is
additive.

### Tests

- `tests/qa/test_v0420_announce_adopt.py` (7 tests):
  full lifecycle, validation rejection, idempotency on repeat
  announces, reject + back-off, UI render.

### Compatibility

- All v0.4.19 routes preserved.
- New `device_announcements` table created via
  `Base.metadata.create_all()` at boot.
- `/api/v1/device/announce` is unauthenticated by design — same
  trust posture as `/api/v1/device/register` (both rely on the
  enrolment-token contract).

## [0.4.19] - 2026-05-10

### Added — Operator visibility upgrades while firmware-side debug continues

- **Tier-1 A: Per-firmware-version fleet breakdown.** New
  `firmware_version_breakdown()` service + collapsible card on
  `/app/devices`. Groups the fleet by `firmware_version`, marks
  the majority cohort green, flags any minority cohorts as
  "outliers" with an amber badge. Operator can spot upgrade
  drift at a glance. Excludes QA fixtures from the calculation
  regardless of the show-fixtures toggle. Devices with no
  reported firmware bucket as `(unknown)`.
- **Tier-1 B: On-disk firmware reconciliation.** New
  `discover_on_disk_releases()` service walks
  `data/firmware/<channel>/` for `.bin` files not already in
  `firmware_releases`, computes SHA + size, backfills DB rows
  and mirror records. Closes the gap between the firmware
  team's direct-to-disk artifact placement and the admin UI's
  Firmware page. Idempotent. Audit-logged as `firmware.scanned`.
  UI: "Scan now" button on `/app/firmware`. API:
  `POST /api/v1/admin/firmware/scan`.
- **Tier-1 C: Real watchdog rules for the lab fleet** (configured
  post-deploy via the API, not in the codebase).

### Compatibility

- All v0.4.18 routes preserved.
- New endpoint `POST /api/v1/admin/firmware/scan` — admin role.
- New section card on `/app/devices` is purely additive; existing
  table + filters unchanged.

## [0.4.18] - 2026-05-09

### Fixed

- **BUG-050 — Device register 500 on overlong caller-supplied
  fields.** Pre-fix, sending `display_name` >120 chars (or
  `hardware_model` >80, `mac_address` >40, etc.) hit the
  Postgres `StringDataRightTruncation` on INSERT → unhandled
  → 500. Now: validation-failed → 400 with field name + max
  length.
- **BUG-051 — Device register accepted any string as
  `mac_address`.** `<script>alert(1)</script>` was happily
  persisted. Output rendering is Jinja-escaped so no XSS
  surface, but admins saw garbage in the MAC column. Now:
  hex-only validation regex (`[0-9A-Fa-f:.\-\s]+`) rejecting
  anything outside the common formats.

### Compatibility

- All v0.4.17 routes preserved.
- Service-layer validation only — existing devices with
  legacy MAC strings (none in production today; we just
  cleaned the fleet) pass through unchanged.

## [0.4.17] - 2026-05-09

### Added
- **`DELETE /api/v1/admin/enrollment-tokens/<id>`** (BUG-044).
  API consistency with the UI POST `/app/.../revoke` endpoint.
  Returns 200 + `{deleted: true}` on success, 404 on
  not-found-or-already-consumed.
- **End-to-end schedule runtime test** at
  `tests/qa/test_v0417_schedule_runtime_e2e.py`. ~120s wall-
  clock test that exercises a one-shot maintenance schedule
  through the full lifecycle: schedule fires → maintenance
  flag flips ON → window ends → flips OFF.

### Fixed
- **BUG-048 — HTTP watchdog probe followed redirects.** Pre-fix
  treated 302 as a failure. Real-world health checks often
  redirect (HTTPS upgrades, CDN routing, app entrypoints). Now
  follows up to 3 redirects and treats a final 2xx as success.
  Operators using `/health` URLs that 302 to `/login` no longer
  see false-positive "down" alerts.

### Compatibility

- All v0.4.16 routes preserved.
- Probe behavior change is operator-friendlier: rules that were
  silently failing (false negatives) on redirecting URLs will
  now correctly probe success.

## [0.4.16] - 2026-05-09

### Fixed — Bootstrap admin password no longer reverts on container restart (BUG-046)

- **BUG-046 — `ensure_bootstrap_admin` overwrote the password on
  every startup.** Pre-fix: any time the container restarted
  (image update, host reboot, `--force-recreate`), the bootstrap
  admin's password got force-set back to
  `REBOOTER_BOOTSTRAP_ADMIN_PASSWORD`. This silently nuked any
  password the operator had legitimately reset via the
  `/app/reset-password` flow. Worst-case-followed: operator
  resets to a new password, container recreates next morning,
  new password stops working, operator does another reset, and
  so on.
- **Default behavior changes**: startup only sets the password
  on initial create. Privileges (`is_admin`, `is_super_admin`,
  `is_active`) are still reconciled every startup so an operator
  can never lock themselves out of admin.
- **Recovery path preserved** behind a new opt-in env var:
  `REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP=1`. Set
  this when you've forgotten your password — restart with the
  env var set, log in with the env-var password, then unset the
  env var so subsequent restarts don't keep clobbering.

### Compatibility

- All v0.4.15 routes preserved.
- The default behavior change is intentional and operator-
  safer. To opt into the legacy "always force-reconcile"
  behavior, set the new env var.

## [0.4.15] - 2026-05-09

### Fixed — Forgot-password page lied when SMTP failed (BUG-045)

- **BUG-045 — Forgot-password page told the user "we've emailed
  you a link" even when the SMTP send blew up.** Pre-fix:
  v0.4.6 (BUG-030) caught the SMTP exception so the request
  didn't 500, but the user-facing message stayed cheerful and
  identical to the success path. Users sat waiting for an email
  that never arrived.
- Now: when `smtp_ok=False` AND the email IS registered (token
  was minted), the page renders an additional warning panel with
  the SMTP-error class name (`SMTPConnectError`,
  `SMTPRecipientsRefused`, etc.) and a "contact your admin" hint.
- Disclosure delta is acceptable: the page already echoes a
  masked email back to the user, which proves the form processed
  their input. The smtp-status difference between known/unknown
  email is small relative to that.

### Compatibility

- All v0.4.14 routes preserved.
- Pure UI / message-rendering change.

## [0.4.14] - 2026-05-09

### Operational

- **Database cleanup.** Purged 130 QA-fixture devices, 1522
  enrollment-token leftovers, 66 noise rows in
  `unregistered_auth_attempts`, 83 QA-prefixed groups, 25
  QA-prefixed sites. The cluster is now in a pristine zero-row
  state across every device-related table — operator can see the
  truth: zero real devices have ever come online. Status page
  now reads "No devices yet".

### Fixed

- **BUG-042 — Watchdog rule serializer missing v0.4.2 runtime
  state.** The `serialize_rule` shape was written in v0.4.0,
  before v0.4.2 added `failure_streak`, `recovery_streak`,
  `last_probed_at`, `last_action_at`, `last_outcome` columns.
  UI templates already referenced these (rendered as empty
  strings); JSON consumers got KeyError. Now: serializer
  exposes all five.
- **BUG-043 — `POST /api/v1/admin/enrollment-tokens` ignored
  `ttl_seconds` parameter.** Service used the env-var
  `REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS` (default 24 h) as the
  only knob. Operators wanting a 30-day token for a firmware-
  team handoff had to recreate the container with a bumped env
  var. Now: `ttl_seconds` honored, capped at 30 days.

### Test coverage

- New `tests/qa/test_v0414_watchdog_runtime_e2e.py` (3 tests):
  end-to-end against the real APScheduler tick — failing TCP
  probe → action_fired → cooldown_skip transitions; succeeding
  HTTP probe → no action; maintenance window suppresses firing.
  Wall-clock ~25 s per test; skip via `SKIP_E2E=1` in
  budget-constrained CI.

### Compatibility

- All v0.4.13 routes preserved.
- BUG-042 fix is additive — old fields still present, new ones
  added alongside.
- BUG-043 fix is additive — `ttl_seconds=null` keeps the env-var
  default behavior.

## [0.4.13] - 2026-05-09

### Fixed — schema validation hardening (BUG-038, 040, 041)

- **BUG-038 — Rule target requires a concrete identifier.**
  `target={"kind":"device"}` (no `id`) was accepted; runtime
  silently no-ops. Now: returns 400 unless `id` (device/group)
  or `tag` is present and non-empty.
- **BUG-040 — Schedule weekly weekdays deduped + sorted.**
  Pre-fix: `weekdays=[5,5,5,5]` rendered "Sat, Sat, Sat, Sat".
  Now: stored as `[5]`.
- **BUG-041 — Schedule weekly weekdays must be 0..6.** Pre-fix:
  `weekdays=[99]` accepted, sentence rendered an empty day list,
  schedule never fired.

### Compatibility

- All v0.4.12 routes preserved.
- Existing rules with empty target.id continue to load (we only
  validate on create — historical rows pass through unchanged).

## [0.4.12] - 2026-05-09

### Fixed — input validation hardening (BUG-035, 036, 037)

Found during the v0.4.11 iteration probe.

- **BUG-035 — Watchdog rule numeric thresholds now bounded.**
  `failure_threshold` and `recovery_threshold` 1..100;
  `window_seconds` 5..86400; `cooldown_seconds` 0..86400.
  Pre-fix: `failure_threshold=-1` made every probe fire the
  rule's action immediately on the first failure (the
  `failure_streak < failure_threshold` gate was always False).
- **BUG-036 — Watchdog rule + schedule names now capped at 120
  chars in the service.** Pre-fix: 121-char names hit Postgres
  `value too long for type character varying(120)` → 500.
- **BUG-037 — Portal-wide maintenance `reason` capped at 200
  chars** (truncated with "..." suffix). Pre-fix: a 5KB reason
  rendered as a wall of text in the Status banner.

### Compatibility

- All v0.4.11 routes preserved.
- Pure validation-layer changes; no schema or runtime-state
  changes.

## [0.4.11] - 2026-05-09

### Security

- **BUG-033 — Standard security headers on every response.**
  After-request hook attaches `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`,
  `Strict-Transport-Security: max-age=15552000;
  includeSubDomains`, plus a conservative CSP allowing same-
  origin scripts/styles + inline (Jinja templates have inline
  `<script>` blocks) and `frame-ancestors 'none'`. None of the
  headers were present pre-v0.4.11.

### Fixed

- **BUG-034 — `POST /api/v1/admin/schedules` with malformed
  `at_time_utc` returned 500.** The column is `VARCHAR(5)`;
  values like `"not-a-time"` (10 chars) failed the Postgres
  insert with `DataError` → unhandled → 500. Now: validates
  the `HH:MM` shape (`00:00`–`23:59`) in the service layer and
  returns 400 `validation_failed` with a friendly message.

### Compatibility

- All v0.4.10 routes preserved.
- Security headers use `setdefault` — any blueprint that has
  set its own header keeps it.

## [0.4.10] - 2026-05-09

### Security

- **BUG-005 — Server-side cookie + JWT revocation now ENFORCED.**
  Previous behaviour (v0.2.10–v0.4.9): the auth path wrote
  session rows on issuance and `revoke_one`/`revoke_all_for_user`
  set `revoked_at`, but the middleware ignored those rows. A
  cookie or access-token exfiltrated before logout could keep
  authenticating until its hard expiry. Now: the middleware
  consults `sessions.revoked_at` on every authenticated request.
  Revoked rows are treated as unauthenticated. Legacy cookies
  with no `sid` claim still work (graceful fallback).

### Fixed

- **BUG-031 — JSON-rule editor preserved input on validation
  failure.** Previously a typo or validation error redirected
  away and the operator's pasted JSON was lost. Now the form
  re-renders with the JSON pre-filled and the error inline.
  Saves a re-paste on every typo.
- **BUG-032 — Schedule-vs-operator maintenance race.** If the
  operator manually toggled portal-wide maintenance OFF during
  a scheduled-maintenance window, the schedule_tick reconciler
  would re-enable it ~30 s later. Now: operator toggles stamp
  `operator_override_at` and the reconciler respects that for
  the rest of the active window.

### Bug-log housekeeping

Five "open" bugs were already fixed in earlier releases — the
bug-log tracker had simply gone stale. No code change needed
beyond status updates:
- **BUG-007** — Group/site name uniqueness — already enforced
  via DB unique constraint + 409 `name_conflict` error path.
- **BUG-008** — 0-byte firmware upload — already rejected with
  `ValueError → 400`.
- **BUG-009** — Favicon 404 — favicon shipped + `<link rel="icon">`
  in layout.
- **BUG-010** — PATCH ignores unknown fields — already returns
  400 `validation_failed` with the allowed-fields list.
- **BUG-011** — Empty PATCH bumps `updated_at` — already
  short-circuits when the diff is empty.

### Compatibility

- All v0.4.9 routes preserved.
- Auth-middleware change is additive: legacy cookies/tokens
  without a `sid`/`jti` continue to authenticate. Only revoked
  rows are denied.
- Pure code-path fixes; no schema changes.

## [0.4.9] - 2026-05-09

### Added — Watchdog rule JSON editor (B9) + bulk-action per-device audit (B14)

- **JSON editor** on `/app/rules` for shapes the form-builder
  can't express (custom probes, multi-window maintenance,
  complex escalation chains). Same body shape as the v0.4.0 API.
  Round-trip lossless. Audit-logged with `via=json_editor`.
- **Per-device audit fanout** for bulk actions. New helper
  `audit_service.record_per_device(action, device_ids, ...)`.
  Wired into:
  - `device.bulk_deleted_per_device` — one row per deleted
    device on bulk-delete from the Devices page.
  - `device.bulk_delete_skipped_per_device` — for protected
    devices that the bulk-delete refused to touch.
  - `device.mass_command_issued_per_device` — one row per
    device when a group command fans out.
  - `device.mass_command_skipped_per_device` — for protected
    devices skipped during a group fan-out.
  The aggregate meta-row (`device.bulk_deleted`,
  `group.mass_command_issued`) still emits — these are
  *additional* rows that make "what did this bulk-delete
  actually touch?" answerable from `/app/audit?target_id=<dev>`.

### Compatibility

- All v0.4.8 routes preserved.
- New audit-action names; existing meta-action queries unchanged.

## [0.4.8] - 2026-05-09

### Added — Schedules as a separate primitive (B8)

Time-driven counterpart to watchdog rules. Rules fire on probe
failure; schedules fire on time.

- **New table `schedules`** with `kind`, `recurrence` (once /
  daily / weekly), `at_time_utc`, `weekdays`, `duration_seconds`,
  `target`, plus runtime state (`last_run_at`, `next_run_at`,
  `last_outcome`).
- **Two kinds:**
  - `power_cycle` — enqueues `relay_cycle` against a target
    (device / group / tag) on the schedule.
  - `maintenance` — flips portal-wide watchdog maintenance ON
    for `duration_seconds`, then OFF (so e.g. "every Sat 2-3 am
    UTC, suppress watchdog rules" is one-line).
- **APScheduler `schedule_tick`** every 30 s. Fires due
  schedules, recomputes `next_run_at` for recurrences,
  reconciles the maintenance flag.
- **UI at `/app/schedules`** with form for all the shapes +
  enable/disable/delete + plain-English sentence render.
- **API:** `GET / POST / DELETE /api/v1/admin/schedules`.
- **Audit hooks:** `schedule.created`, `schedule.deleted`,
  `schedule.enabled_changed`.
- **Cross-link** between `/app/rules` and `/app/schedules` in
  the page header (no top-nav change).

### Operational controls

- `REBOOTER_SCHEDULER_DISABLED=1` already short-circuits the
  whole APScheduler — same flag covers the new schedule_tick.

### Compatibility

- All v0.4.7 routes preserved.
- New `schedules` table created via `Base.metadata.create_all()`
  at boot.

## [0.4.7] - 2026-05-09

### Added — Maintenance windows + portal-pause + watchdog.firing inbox (B7 + B13)

- **Portal-wide watchdog maintenance toggle.** Super-admin
  toggles via the Status page (or
  `POST /api/v1/admin/maintenance`). When ON, the watchdog tick
  short-circuits — no probes, no actions. Operator can do a
  scheduled cabinet reboot without false-positive firing. ON-state
  shows a banner on the Status page; OFF-state shows a collapsed
  "pause all" form. Audit hook: `maintenance_mode.toggled`.
- **Per-rule maintenance windows.** Each rule's existing
  `maintenance_windows` JSON is now honored by the runtime. Rule-
  create form gets a "From/To" datetime-local pair (treated as
  UTC). During the window the runtime records
  `maintenance_skip` events instead of firing the rule's action.
- **Status inbox surfaces watchdog.firing rules.** Any rule with
  `status='firing'` OR an `action_fired` event in the last hour
  shows up as an attention item, severity warn, ranking between
  `device_offline_long` and `device_failsafe`. Click target is
  `/app/rules#<rule-id>` (rule-list anchor).

### New table: `runtime_flags`

Tiny key/value store for flags the operator must change without a
redeploy. Today only `maintenance_mode_active` lives here; future
operator-toggles (e.g. "freeze fleet during incident") will share
the table.

### Compatibility

- All v0.4.6 routes preserved.
- New tables created via `Base.metadata.create_all()` at boot.
- Rules created pre-v0.4.7 have empty `maintenance_windows`; runtime
  treats empty as "always run" — no behavior change.

## [0.4.6] - 2026-05-09

### Fixed — forgot-password handler crashes on SMTP failure (BUG-030)

- `forgot_password_submit` was calling `send_password_reset_email`
  WITHOUT a try/except. When the configured SMTP server hangs
  up mid-handshake (currently the case on prod —
  smtpauth.earthlink.net auth fails because the SMTP password is
  set to the bootstrap admin's app password rather than the
  EarthLink SMTP password), the SMTPServerDisconnected bubbled
  out to Flask → 500.
- Now: SMTP failures are caught and logged; the password-reset
  token is still minted in the DB, the audit-log entry records
  `smtp_ok=false` + `smtp_error=<exception name>`, and the user
  sees the same non-disclosing "if an account exists, we've
  emailed you" page as before. Operator can recover the URL from
  audit history.

### Operational note

Until the operator updates `REBOOTER_SMTP_PASSWORD` to a real
EarthLink SMTP credential, password-reset emails will not be
delivered. The forgot-password flow no longer 500s, but no email
arrives. Settings → Notifications → "Send test email" surfaces
the same SMTP error to the operator immediately.

## [0.4.5] - 2026-05-09

### Fixed — concurrent firmware upload regression (BUG-002a)

- **Concurrent firmware upload race returned 500 (regressed v0.3.9).**
  The IntegrityError cleanup branch in `upload_release` referenced
  `pointer_path`, which v0.3.9 deleted when the channel pointer
  switched from a static file to a Flask redirect. The loser thread
  of a concurrent upload race hit `NameError: pointer_path` →
  unhandled → 500. Originally fixed in v0.1.3 (BUG-002), regressed
  in v0.3.9; now fixed properly.
- **Stale-cookie name in `test_logout_does_not_revoke_cookie_server_side`.**
  Was reading `s.cookies.get("session")` — now reads
  `rebooter_session` (with fallback to legacy name during deploy
  transitions).
- **Rate-limit test gracefully skips on exempt source.** When the
  test's source IP is in `REBOOTER_RATE_LIMIT_EXEMPT_IPS` (the
  default exemption that lets the QA host run a full suite without
  hitting the per-IP cap), the test detects this via the absence
  of `X-RateLimit-Limit` headers and emits `pytest.skip` instead
  of failing.
- **`test_logout_does_not_break_subsequent_login` switched to
  `disposable_admin_session`.** Was bumping the bootstrap admin's
  `tokens_valid_after` mid-suite, cascading into the v0.2.x test
  failures. Now uses a fresh admin user.

### Compatibility

- All v0.4.4 routes preserved.
- Pure code-path fix in firmware service; no schema changes.
- The 500-on-race fix is purely defensive — removing a NameError
  in an error-recovery branch.

## [0.4.4] - 2026-05-09

### Test-infrastructure hardening (BUG-021 / 024 / 025 / 026)

Closes the four test-infra defects from the post-v0.4.2 deep
regression. The QA suite now runs clean against the live deployment
without cascade failures.

- **BUG-021 — disposable_admin_session fixture.** Tests that mutate
  auth state on the admin user (call `/api/v1/auth/logout`,
  redeem a password reset, run revoke-all) now use a fresh,
  fixture-managed admin user instead of poisoning the
  bootstrap admin's `tokens_valid_after`. The session-scoped
  `admin_token` fixture stays — but no test file calls
  `/api/v1/auth/logout` on the bootstrap admin anymore.
- **BUG-024 — stale tests updated.** `test_login_logout_round_trip`
  now asserts page title contains "Status" (v0.3.1 R-DSH-1).
  `test_create_group_does_not_log_user_out` checks for
  `rebooter_session` cookie name (v0.3.3 cookie-domain rework).
- **BUG-025 — rate-limit test gets `@pytest.mark.timeout(120)`.**
  Includes a 65 s post-burst sleep to clear the per-minute window
  for downstream tests.
- **BUG-026 — invitation-redeem cookie name fixed.** Asserts
  `rebooter_session` not `session`.

### New env var: `REBOOTER_RATE_LIMIT_EXEMPT_IPS`

Comma-separated list of client IPs whose requests bypass the
rate limiter. Used for the QA test host so a full suite run
(~50 logins) doesn't burn through the per-IP 200/hour budget.
**NEVER set this for arbitrary client IPs in production.**
Implemented via Flask-Limiter `request_filter` which short-
circuits the entire decorator chain when the IP matches.

### Compatibility

- All v0.4.3 routes preserved.
- New env var has a safe default (`192.168.18.1,127.0.0.1` —
  docker bridge gateway + loopback) which is harmless in any
  internet-facing deployment because those addresses can never
  appear as a real client IP after `ProxyFix(x_for=1)` parses
  `X-Forwarded-For`.

## [0.4.3] - 2026-05-09

### Fixed — Quick wins from the post-v0.4.2 deep regression

- **BUG-022 (high) — Sign out link added to header.** The
  redesign collapsed the header and Sign out ended up only on the
  Profile page. Restored it to `topbar-actions`.
- **BUG-023 (medium) — Role badge added to header.** Super-admin
  shows a red `super admin` badge; admin shows a neutral `admin`
  badge. Helps the operator see the elevated role at a glance —
  important since super-admin actions affect the whole fleet.
- **BUG-028 (low) — Authentication-tab page title.** Changed from
  "Auth settings" to "Authentication settings" so the title
  matches the visible tab label.

### Compatibility

- Pure UI / template changes. No model, route, or API changes.

## [0.4.2] - 2026-05-09

### Added — Watchdog probe runtime (B6 from BACKLOG)

Watchdog rules created in v0.4.0 now actually FIRE.

- **APScheduler watchdog tick** every 10 s. For each enabled rule
  whose `last_probed_at + window_seconds` has elapsed, runs the
  probe and writes a `WatchdogProbeEvent`.
- **Probe kinds shipped** (stdlib only):
  - `internet` — TCP connect to 1.1.1.1:53.
  - `tcp` — TCP connect to host:port.
  - `ping` — falls back to TCP-port-80 to host (no raw ICMP from
    container by default; native-ICMP queued).
  - `http` — `GET <url>`, success on 2xx.
  - `dns` — resolve hostname.
  - `gateway` — no-op until device firmware reports its LAN gateway
    in heartbeat (queued for v0.4.3+).
- **State machine.** failure_streak / recovery_streak / status
  / last_probed_at / last_action_at / last_outcome stored on the
  rule row (idempotent ADD COLUMN at startup).
- **Action dispatch.** When `failure_streak >= failure_threshold`
  AND outside cooldown:
  - `cycle` → enqueues `relay_cycle` for each device in the target.
  - `hold_off` → enqueues `relay_off` + sets `is_held_off`.
  - `notify_only` → no power action (audit only).
- **Recovery.** `recovery_threshold` consecutive successes after a
  failure clears the streaks and re-arms the rule.
- **Cooldown.** During cooldown failures still log
  (`outcome=cooldown_skip`); the action does not fire again.
- **Probe-now diagnostic.** UI button + API
  `POST /api/v1/admin/rules/<id>/probe-now` runs a single probe
  synchronously and logs an event. Does NOT advance state or fire
  actions — purely operator-facing.
- **Per-rule event log.** New API `GET /api/v1/admin/rules/<id>/events`
  returns the last 50 events (newest first). Inline expander on
  the rule list shows the latest 10.

### Operational controls

- `REBOOTER_WATCHDOG_DISABLED=1` — emergency-stop the runtime
  without touching code (the tick is a no-op).

### Audit hooks

- `watchdog_rule.probed` (per probe-now invocation; per scheduled
  tick events go through the WatchdogProbeEvent log, not audit).

### Compatibility

- 5 new columns on `watchdog_rules` (failure_streak, recovery_streak,
  last_probed_at, last_action_at, last_outcome) added via the
  idempotent ADD COLUMN bootstrap. No migration step.
- All v0.4.1 routes preserved.
- Tests for the runtime exercise the synchronous probe-now path
  (10 tests).

## [0.4.1] - 2026-05-09

### Added — Password reset + Notifications tab + 30-day invite default

- **Password-reset flow.** New `password_resets` table; new
  `/app/forgot-password` and `/app/reset-password` pages. Tokens
  default to **1 h TTL** (configurable via
  `REBOOTER_PASSWORD_RESET_TTL_SECONDS`). On consume,
  `tokens_valid_after` is bumped so every existing session/JWT
  for that user is invalidated. "Forgot your password?" link added
  to the login page.
- **Settings → Notifications** tab. Read-only display of env-var
  SMTP config + a "Send test email" form.
- **Invitation default TTL: 7 → 30 days** (operator-requested).
  Override via `REBOOTER_INVITATION_TTL_SECONDS`. Invite email body
  updated to match.
- **Email service additions:** `send_password_reset_email`,
  `send_test_email`.

### Audit hooks

- `password_reset.requested` (logged for every attempt, including
  non-existent emails, so the operator sees enumeration probes).
- `password_reset.consumed` (per-user, includes IP).
- `smtp.test_sent` (per-test, includes target email + ok flag).

### Compatibility

- All v0.4.0 routes preserved.
- `password_resets` table created via `Base.metadata.create_all()`
  — no migration step.
- SMTP behavior unchanged when not configured: invitations still
  succeed (link surfaces in the admin console); password-reset
  request silently succeeds-shaped (the email simply never arrives).

## [0.4.0] - 2026-05-09

### Added — Watchdog rules first slice (P4 of webui-redesign-plan)

Net-new feature surface. v0.4.0 ships **data model + CRUD +
plain-English render**; the probe runtime that actually executes
rules and writes events is queued for v0.4.1+.

- New tables: `watchdog_rules` (full schema per
  `webui-redesign-plan.md` §7.1) + `watchdog_probe_events`
  (table shape only; inserts come in v0.4.1+).
- Plain-English rule renderer (R-WD-1). Every rule shows its
  full sentence form on the list page:
  > *"If ping to `192.168.1.1` fails 3 consecutive times over
  > 60 s, power-cycle (5s off) on device `Office Modem`, then
  > wait 5 min and check 2 successes before re-arming."*
- Rule-builder UI at `/app/rules` replaces the v0.3.0
  empty-state stub. Form picks probe kind (internet / ping /
  tcp / http / dns / gateway), action (cycle / hold_off /
  notify_only), target (device / group / tag), and thresholds.
- Per-rule enable/disable + delete actions.
- New API: `GET /api/v1/admin/rules`, `POST` (admin+), `DELETE`.
- Audit hooks: `watchdog_rule.{created,deleted,enabled_changed}`.

### NOT in v0.4.0 (queued)

- Probe runtime — rules ARE stored but DO NOT FIRE yet (UI
  flags this).
- Per-rule event log UI, probe-now / simulate buttons.
- Maintenance windows UI + portal-wide maintenance-mode.
- Schedules as a separate primitive.
- Notifications on rule trigger (gated on v0.4.1 email-SMTP).

### Compatibility

- All v0.3.9 routes preserved.
- New tables via `Base.metadata.create_all()` at boot — no
  migration step.

## [0.3.9] - 2026-05-09

### Added — firmware mirror chain P1 (RFC-002)

Backend hosting for the per-channel firmware library that
RFC-005's safe-bootstrap and the dual-URL fallback design depend
on.

- **New table:** `firmware_release_mirrors` — one row per
  (release, mirror-kind) tuple. Tracks URL, status (`pending` /
  `live` / `failed`), `verified_sha256`, and probe metadata.
  Cascade-deletes with the parent `firmware_releases` row.
- **Per-channel publishing on upload.** A new release's binary
  is written to **two** static locations on the firmware volume:
  - canonical flat: `<firmware_dir>/rebooter-<v>.bin` (existing,
    kept for backwards-compat with devices already in the field)
  - per-channel: `<firmware_dir>/<channel>/rebooter-<v>.bin`
- **Channel pointer is a Flask 302-redirect endpoint, NOT a static
  file.** New public, unauthenticated route:
  `GET /api/v1/firmware/<channel>/latest` → 302 to
  `<public-base>/<channel>/<latest-version-filename>.bin`. RFC-005's
  bootstrap firmware will fetch this on first boot and on every
  retry — it always resolves to the freshest binary in the
  channel without the bootstrap needing to know specific version
  strings. The endpoint is public (no auth) because the
  bootstrap doesn't have a bearer token yet by definition.
- **Why redirect, not static file.** Static `latest.bin` files
  were considered and rejected: they would collide with nginx's
  global `open_file_cache_valid 60s`, making an overwrite
  invisible to clients for up to a minute. The redirect endpoint
  queries the DB on every hit, so it's always fresh; nginx still
  serves the per-channel versioned binary on the redirected URL
  (which never changes content for the same path → safe to cache).
- **Mirror records.** Three rows per upload — canonical flat
  URL, per-channel static-file URL, and channel-pointer redirect
  URL — all marked `status=live` / `verified_sha256=<hash>` since
  we just wrote them.
- **Admin UI.** `/app/firmware` now shows a per-release mirror
  expander listing each URL + status + kind.
- **Delete cleans up cleanly.** Deleting a release removes the
  canonical + per-channel artifacts. The channel-pointer URL
  self-updates because it's DB-backed.

### What's NOT in this release

Per RFC-002 §8 phase split:
- **GitHub Releases mirror publisher (P2)** — not in v0.3.9.
  The `MirrorPublisher` abstraction is intentionally not yet
  introduced; the local-only logic is inline. Once we add the
  GitHub publisher, we'll abstract.
- **Project-owned nginx snippet (RFC-002 §7.6)** — host nginx
  config still hand-edited; project snippet ships in a follow-up
  minor.

### Compatibility

- All v0.3.8 routes preserved.
- Existing flat-layout firmware URLs continue to work — devices
  in the field do not need re-configuration.
- New table created via `Base.metadata.create_all()` at boot.
  No manual migration step.
- Per-channel publish failure logs but does not fail the
  upload; the canonical flat-layout file remains the source of
  truth for v0.3.9.

## [0.3.8] - 2026-05-09

### Added — failsafe-event surface (RFC-005 P1 backend)

Receives device-side failsafe reports per RFC-005 §5.2. When a
device falls back from slot B (just-OTA'd main) → slot C (last-
known-good) it POSTs to the new endpoint and we surface the
event prominently.

- **New table:** `device_failsafe_events` with columns
  `device_id`, `received_at`, `failed_version`,
  `fallback_to_version`, `reason`, `details` (JSON-shaped, opaque
  to the backend so future firmware extensions don't require a
  schema change).
- **New endpoint:** `POST /api/v1/device/failsafe`
  (device-token-authenticated). Body shape:
  ```
  {
    "device_id": "...",
    "failed_version": "0.x.y",
    "fallback_to_version": "0.x.z",
    "reason": "boot_failure" | "sha256_mismatch" |
              "watchdog_reset" | "timeout" | "other",
    "details": { ... }
  }
  ```
  The `device_id` in the body is informational; we trust the
  bearer token's device. Best-effort write — never blocks the
  device's POST.
- **Status inbox attention items.** New
  `device_failsafe` kind with severity `critical`. Renders with
  a red-accent treatment (new `.v3-sev-critical` CSS class).
  Surfaces every failsafe in the last 24 h. No threshold; a
  failsafe is a strong signal on its own.
- **Per-device Failsafe section** on the device-detail page
  (new tab anchor `#failsafe`). Shows the last 25 failsafe
  events with the failed/fallback versions, reason, and an
  expandable diagnostic blob.
- **`get_device_detail()`** returns `failsafe_events`
  alongside `audit_history`.

### Why this matters

Pairs with the (firmware-team-side) self-healing OTA design in
RFC-005. When a firmware update doesn't boot on a real device,
the device tells central; central tells the operator
prominently; the operator can then push a fixed version. The
machinery is "no firmware update can brick a device" — the
RFC-005 constitutional invariant.

### Compatibility

- All v0.3.7 routes preserved.
- New table created via `Base.metadata.create_all()` at boot.
  No manual migration step.
- The existing inbox shape gains `totals.failsafe` and a new
  attention-item kind `device_failsafe`. Existing API consumers
  that iterate `attention` by their existing kind set are
  unaffected.

## [0.3.7] - 2026-05-09

### Fixed — `ERR_TOO_MANY_REDIRECTS` on stale cookie

**Operator-reported.** Browser hits `/app/`, gets a redirect loop.

**Root cause.** A two-handler interaction the v0.2.x code has had
all along but was rarely exposed:

1. `admin_required_ui` calls `_resolve_user()`. If the cookie
   carries a `user_id` for a deleted-or-deactivated user, OR if
   the cookie's `iat` is older than the user's
   `tokens_valid_after` cutoff (e.g. somebody triggered
   `revoke_all_tokens` → logout), `_resolve_user()` returns `None`.
   The middleware redirects to `/app/login`.
2. `login_page` then sees `session.get("user_id")` is still
   truthy (the cookie is still in the browser, just stale by the
   freshness check), and redirects back to `/app/`.
3. Loop. Browser eventually surfaces `ERR_TOO_MANY_REDIRECTS`.

This presented today after a QA test triggered logout (which
bumps `tokens_valid_after`) on the operator's user; the
operator's already-cached browser cookie was now older than the
new cutoff.

**Fix.** Two-sided defensive:

- `app/middleware/admin_auth.py::_resolve_user` — when it
  decides the cookie is stale (user can't load, user is
  deactivated, or `iat < tokens_valid_after`), it now **clears
  the session** before returning `None`. Subsequent requests
  see no `user_id` and behave correctly.
- `app/blueprints/admin/auth_ui.py::login_page` — instead of
  redirecting on cookie-truthiness alone, calls `_resolve_user`
  first. If the cookie validates → redirect to `/app/`. If not
  → render the login form (and the session has been cleared by
  `_resolve_user`).

Either fix alone closes the loop; both together is
belt-and-braces.

### Fixed — QA-test-generated 401s polluted the Status inbox

Operator-reported. The v0.3.6 attention items were surfacing
synthetic auth failures generated by the QA suite itself —
showing `dev_QA_<n>` and `dev_x` claimed ids from
`192.168.18.1` (the docker bridge gateway) as if they were real
device problems.

**Fix.** `app/services/inbox.py` now filters two kinds of noise
out of `device_auth_rejected` attention items:

- **Machine-internal source IPs:** `127.0.0.1`, `::1`, and
  `192.168.18.1` (docker bridge gateway as seen inside the
  container — by definition NOT a real LAN device).
- **QA-prefixed claimed device ids:** anything starting with
  `qa `, `qa-`, `qa_`, `test-`, `test_`, `playwright`, `dev_qa_`,
  `dev_test`. Mirrors the v0.2.8 `_QA_PREFIXES` list with the
  v0.3.6 test bucket's `dev_QA_*` shape added.

Either condition skips the row from the attention feed. The data
remains in `unregistered_auth_attempts` and is still visible via
`/app/unregistered-devices` for diagnostic purposes — only the
Status-inbox surface filters it.

### Compatibility

- All v0.3.6 routes preserved.
- No schema change.
- A user with a stale cookie will see the login form on next
  request (instead of looping). They re-authenticate as
  expected. No data loss.
- The polluting test rows that were already in
  `unregistered_auth_attempts` will age out of the 60-minute
  lookback window naturally, OR an operator can prune them with
  `DELETE FROM unregistered_auth_attempts WHERE source_ip='192.168.18.1';`.

## [0.3.6] - 2026-05-09

### Added — `device_auth_rejected` attention items on the Status inbox

Per the RCA queue (`docs/rca-2026-05-09-no-device-online.md` §6),
the `unregistered_auth_attempts` tracker has always existed but
was only visible from `/app/unregistered-devices`. v0.3.6
surfaces it on the **Status inbox** so an operator immediately
sees "this device IS trying to call but its token is rejected"
without leaving the home page.

Particularly useful right now while the firmware team debugs
`test-s31-01`'s central poll/heartbeat transport: once the
device starts calling again, any 401 it gets shows up here as a
ranked attention item.

**Trigger criteria.** A `(claimed_device_id, source_ip,
endpoint)` tuple that has been rejected at least
**3 times** in the last **60 minutes**. The 3-hit minimum filters
out single transient bad requests; the 60-minute window matches
the existing dashboard-badge cadence.

**Item shape.**

```
kind:        "device_auth_rejected"
severity:    "warn"
title:       "Device auth rejected (N attempts) on /api/v1/device/<endpoint>"
device_id:   <the claimed_device_id from the rejected request>
device_name: same (no separate display name available)
source_ip:   <the client IP>
since:       <last_seen_at>
hint:        "A device is calling with a stale or unknown bearer
              token. Mint a fresh enrollment token and re-enrol
              the device — the firmware's 401 → re-enroll loop
              should pick it up automatically."
rank:        35   (between offline_short=40 and enrollment_pending=30)
```

**Click target.** Status page now routes
`device_auth_rejected` items to `/app/unregistered-devices`
instead of `/app/devices/<id>` (the claimed device id is by
definition unknown — linking to the device-detail page would
404). All other attention-item kinds keep their existing
device-detail link.

**Totals.**
`inbox.totals.auth_rejected` is the new count surfaced to API
consumers + the Status verdict math. `attention_total` includes
it.

### Compatibility

- All v0.3.5 routes and endpoints preserved.
- No schema change. No new env vars.
- Best-effort: a tracker query failure logs but does not crash
  the Status page.
- Rollback: previous Docker tag.

## [0.3.5] - 2026-05-09

### Fixed — bulk-delete deleted unchecked rows (regression in v0.3.4)

**Symptom (operator-reported).** Master-select-all → uncheck the
non-target rows → click *Delete selected* → **all of them got
deleted**, including the unchecked ones.

**Root cause.** The devices list renders the same row in TWO
layouts (desktop table + mobile card) and both copies have a
checkbox with `name="device_id"`. The master-toggle checked both
copies; when the operator unchecked the visible one, its hidden
pair (the other layout's copy of the same row) stayed checked
and was submitted. Server received the value despite the visible
checkbox being unchecked.

**Fix.**
- `static/js/bulk_select.js` now syncs paired checkboxes by
  `name + value` — toggling one toggles the other in the same
  form.
- All four bulk handlers dedupe their incoming id list as
  defense-in-depth, so even a future stray double-submission
  doesn't inflate counts:
  `app/blueprints/admin/{devices,groups,invitations,enrollment_tokens}.py`.

### Documented — RCA: "no device shows online" (operator-reported)

Investigation findings landed in `docs/rca-2026-05-09-no-device-online.md`.
Summary:

- **Server side: healthy.** `device_heartbeats` insert path
  works; v027 + synthetic-probe smoke confirms a registered
  device + a single `POST /api/v1/device/heartbeat` flips
  `heartbeat_state` to `online` immediately.
- **No real device has called this server in the last 24 h+.**
  Container access logs show only `192.168.18.1` (docker bridge,
  QA tests) on every `/api/v1/device/*` POST.
- **Three of four lab devices are network-unreachable** as of the
  RCA window. 192.168.1.{67, 225, 30} 100% packet loss; .207
  pings but TCP-RST on port 80 (no HTTP service).
- **Per the project pause state, three of four devices are
  `central_management_enabled = false` BY DESIGN** (local-only).
  Only `test-s31-01` (192.168.1.67) was centrally enrolled — and
  it's the one that's now unreachable.

**No code defect on the server.** The fix is operational
(power + Wi-Fi + central-enable the three local-only devices) and
firmware-side (the operator's hint "we may need new firmware for
them too" is consistent with the unreachable-state findings).

### Compatibility

- All v0.3.4 routes preserved.
- No schema change.
- Rollback: `sudo docker pull dblagbro/rebooter-droids:0.3.4 && sudo docker tag … :latest && sudo docker compose up -d --no-deps --force-recreate rebooter-droids`.

## [0.3.4] - 2026-05-09

### Added — bulk-action UI on devices, groups, invitations, enrollment tokens

Per-row checkboxes + master select-all + sticky bulk-action bar on
the four list pages where mass operations are useful.

- **Devices list** — bulk-delete selected devices. Respects the
  v0.3.2 `is_protected` lockout: protected devices are skipped
  unless the operator ticks "override 🔒 protected" in the bulk
  bar. The lock badge appears on every protected row so operators
  see why the count of deleted-vs-skipped diverges.
- **Groups list** — bulk-delete selected groups. Cascade behaviour
  matches the existing per-group delete (memberships go; member
  devices stay).
- **Invitations list** — bulk-cancel selected pending invitations.
  Already-consumed invitations cannot be cancelled (they're audit
  records of a real user redemption); they're surfaced as
  `skipped_consumed` in the result flash.
- **Enrollment tokens list** — bulk-revoke + per-token revoke.
  v0.3.4 adds the **first revoke primitive** for enrollment tokens
  (single + bulk); previously tokens were immutable. Consumed tokens
  cannot be revoked (they're records of a real device's bring-up).

### Mass-action confirmation gate

All four bulk actions go through the existing
`app/services/mass_action.py` gate:
- ≤ 5 targets: simple `confirm()` prompt.
- 6 – 20 targets: simple `confirm()` prompt, count visible.
- > 20 targets: typed-confirmation prompt — operator must echo the
  verb (`delete`, `cancel`, `revoke`).

The submit button auto-promotes to `btn-danger` red styling once
the count crosses 20.

### Frontend foundation

- New `static/js/bulk_select.js` (~120 LOC, vanilla, no deps) —
  wired up via `data-bulk-form` / `data-bulk-master` /
  `data-bulk-row` / `data-bulk-bar` attributes. Progressive
  enhancement: form submit works without JS; the JS adds live
  count, master-toggle (with `indeterminate` state), and
  disable-when-empty.
- New CSS surfaces: `.v3-bulk-checkbox-cell`, `.v3-bulk-master`,
  `.v3-bulk-checkbox`, `.v3-bulk-bar`, `.v3-bulk-bar-count`. The
  bar is `position: sticky` above the bottom-tab nav on mobile.

### API additions

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/v1/admin/devices/bulk-delete` | `{device_ids, override_lockout, confirmation_level, confirmation_typed_value}` | `{deleted, skipped_protected, skipped_unknown}` |
| (groups + invitations + tokens) | UI-only in v0.3.4; API endpoints land in v0.3.5 if a consumer asks. |

All bulk actions emit a single audit row with action ∈
`{device.bulk_deleted, group.bulk_deleted, invitation.bulk_cancelled,
enrollment_token.bulk_revoked}` and `details.reason='operator'`.

### Compatibility

- All v0.3.3 routes and endpoint names preserved.
- No schema change.
- Rollback: `docker run dblagbro/rebooter-droids:0.3.3`.

## [0.3.3] - 2026-05-09

### Fixed — frequent sign-outs when switching between www and www2

The session cookie was host-scoped (no `Domain=` attribute), so a
login at `www.voipguru.org/rebooter` did not carry to
`www2.voipguru.org/rebooter` — every switch required a fresh login.
The firmware-side multi-URL fallback (primary → secondary) made this
fire repeatedly during a single working session.

**Diagnosis confirmed via Playwright** (`/tmp/diagnose_signouts.py`,
captured in this commit's audit trail): cookie domain was
`www.voipguru.org`, hitting `www2.voipguru.org` after login bounced
to `/app/login`.

**Fix.**

- New env var `REBOOTER_COOKIE_DOMAIN`. When set (e.g.,
  `.voipguru.org`), the session + theme cookies carry across all
  subdomains of that domain. Default empty = host-scoped (the
  v0.3.0–0.3.2 behaviour) for self-hosted single-host deployments.
- Cookie name renamed from Flask's default `session` to
  `rebooter_session`. Avoids collisions with peer voipguru.org apps
  (hub, paperless, etc.) that also default to `session`. Without the
  rename, a domain-shared cookie could collide with another app's
  cookie of the same name and produce confusing failures.
- Theme cookie similarly renamed: `theme` → `rebooter_theme`. The
  legacy `theme` cookie is still read for one minor so users don't
  lose their light-mode preference on upgrade; the writer clears it.
- `docker-compose.yml` defaults `REBOOTER_COOKIE_DOMAIN=.voipguru.org`
  for the multi-URL voipguru deployment.

### Operational impact

- **Operators upgrading from v0.3.0–0.3.2 will be signed out exactly
  once** when v0.3.3 is deployed. The old `session` cookie is still
  in their browser but the server is now looking for
  `rebooter_session`. After the one re-login, the new cookie is
  cross-subdomain and switching between www and www2 carries it.
- No schema change. No code-call-site change.

## [0.3.2] - 2026-05-09

### Added — Power controls + safety + lockout (P3 of webui-redesign)

- **`is_protected` lockout flag** on every device (R-DEV-8). When
  set, the service layer rejects any power command (`relay_on`,
  `relay_off`, `relay_toggle`, `relay_cycle`, `device_restart`)
  unless the caller passes `override_lockout=True` (form param or
  JSON body).
  - API: locked-device commands return **HTTP 423 Locked** with
    `error.code = "device_locked"`.
  - UI: device-detail Power tab shows a lockout banner; the
    settings tab carries the toggle.
  - Mass fan-out (group commands) **skips** protected devices
    by default and surfaces the skipped count in the audit row +
    a warning flash. `override_lockout=1` includes them.
- **Hold-off action** (R-CTRL-3). Issues `relay_off` with the
  intent flag `hold_off=1`, sets the device's new `is_held_off`
  bool, and the UI renders a "held off" badge until any power-on
  command (`relay_on` / `relay_toggle` / `relay_cycle`) clears it.
  Watchdog rules + schedules (P4) MUST honour this flag.
- **Cancel-pending-command** (R-CTRL-8). New service helper
  `commands.cancel_pending_command()` flips a queued command to
  `cancelled` status, but only while it's still in `pending` —
  once the device has accepted, the cancel returns 409.
  - API: `POST /api/v1/admin/devices/<id>/commands/<cmd_id>/cancel`
  - UI: cancel button on every pending row in the Power tab.
- **`reason` field** convention (R-CTRL-6). Every power-action
  audit row now carries `details.reason ∈ {operator}`. The
  `schedule` and `watchdog` reasons land in P4 when those
  surfaces ship.
- **Confirmation dialogs scaled to action severity** (R-UX-12,
  R-CTRL-4 v0.3.2 visual tune):
  - `relay_off` and `device_restart` get a `btn-danger` red button
    plus a `confirm()` prompt.
  - `relay_cycle` gets a `confirm()` prompt that names the device.
  - `hold_off` requires a typed-confirmation prompt of the
    device's display name.
- **Lockout banner + held-off banner** on the Power tab —
  prominent visual treatment so operators can't miss the state.
- **`devices.is_protected` patchable** via `PATCH /api/v1/admin/
  devices/<id>` with `{"is_protected": true|false}`. Audit row
  emitted on change.

### Schema

Idempotent boot-time `ALTER TABLE ADD COLUMN IF NOT EXISTS`:
- `devices.is_protected BOOLEAN NOT NULL DEFAULT FALSE`
- `devices.is_held_off  BOOLEAN NOT NULL DEFAULT FALSE`

No manual migration step.

### Compatibility

- All v0.3.1 routes and endpoint names preserved.
- `enqueue_for_group()` now returns `(created, skipped)` instead
  of `list[Command]`. The two callers in `app/blueprints/admin/
  groups.py` are updated. **Internal API only** — no public
  consumer affected.
- Rollback: `docker run dblagbro/rebooter-droids:0.3.1`.

## [0.3.1] - 2026-05-09

### Added — Status page + device list/detail restructure (P2 of webui-redesign)

- **Status page** (`/app/`) replaces the v0.2.x stat-grid dashboard
  with an attention-feed-shaped landing.
  - Single-glance health verdict: **all-clear / attention / degraded
    / unknown** (R-DSH-2). The `unknown` state never crashes the
    page — telemetry failures fall through cleanly.
  - **Attention feed** ranked by severity × recency
    (R-DSH-3). Item kinds: `device_offline_short`,
    `device_offline_long`, `device_never`, `enrollment_pending`.
    Each item carries a stable id so a future ack-action can
    dedupe.
  - Plain-language all-clear statement when nothing is wrong
    (R-DSH-9).
  - Manual emergency controls card (open devices, enrol, groups,
    firmware) (R-DSH-8).
  - Recent-activity feed below the fold links into the new
    History page.
- **New service**: `app/services/inbox.py` with
  `health_and_attention()` — single DB hit returns verdict + items
  + totals so the Status page renders consistently.
- **Saved-filter chips on Devices** (R-DEV-4):
  - `Offline > 24 h` · `Never heartbeated` · `Has pending commands`
    · `QA fixtures only`
  - URL round-trip via repeated `?chip=...` query params
    (R-DEV-5). Multiple chips compose with AND semantics.
  - Service-layer support in `app/services/devices.list_devices`.
  - Same chip param on `/api/v1/admin/devices` for API consumers.
- **Mobile card layout** for the devices list (R-DEV-3).
  Breakpoint ≤ 640 px renders devices as stacked cards with the
  primary action reachable without horizontal scroll. Desktop
  unchanged (table layout).
- **Central-vs-local cue** (R-DEV-2): a `local-only` badge on
  devices that opt out of central management, plus a `central`
  badge on devices that opt in. Closes the v0.2.x failure mode
  where local-only devices were rendered as if they were
  unhealthy.
- **Open-this-device's-local-UI** link on device-detail (R-DEV-11)
  when the local IP is known.
- **Device detail tab strip** (R-DEV-7): Overview / Power /
  Watchdog / Schedule / Audit / Events / Settings. Watchdog +
  Schedule are stubs in v0.3.1 with empty states pointing at P4.
- **Enrollment wizard** at `/app/devices/new` (R-DEV-6):
  one-step minting + display of the token + the firmware-side
  configuration block (central_base_url, secondary_base_url,
  enrollment_token). QR-code support deferred to v0.3.2.
- New CSS surfaces: `.v3-verdict-{all-clear,attention,degraded,unknown}`,
  `.v3-attention-list`, `.v3-chips` + `.v3-chip-active`,
  `.v3-device-card`, `.v3-tabbar`. All theme-token-driven so
  they work in light, dark, and system modes.

### Test coverage

- New `tests/qa/test_v031_status_and_devices.py`.
- All v0.2.x and v0.3.0 buckets remain green against live.

### Compatibility

- All v0.3.0 routes and behaviour preserved. The old
  `dashboard.html` template is no longer rendered (the handler
  now renders `status.html`); no template change is breaking.
- All existing endpoint names (`admin_ui.*`, `admin_api.*`)
  preserved.
- No schema change. No new env vars.

## [0.3.0] - 2026-05-09

### Added — design system + layout + navigation foundation (Phase 1 of webui-redesign-plan)

- **New 5-item top navigation:** **Status / Devices / Rules / History
  / Settings**. Replaces the previous one-link-per-database-concept
  nav. The nav reflects operator jobs-to-be-done from
  `docs/webui-redesign-research.md` peer-product analysis (UniFi
  + WattBox + Tailscale shapes).
- **Mobile-first layout:** bottom-tab nav at ≤ 640 px viewport;
  top nav at ≥ 768 px. Both render simultaneously into the same
  five destinations.
- **Mobile-first stylesheet** rewrite (`static/css/app.css`).
  CSS custom properties as theme tokens; light / dark / system
  themes via `data-theme` attribute on `<html>`.
- **Theme picker** at `/app/settings/theme` — system / light /
  dark, persisted as a per-browser cookie (so an operator can
  keep light at the office and dark at home). FOUC-free via an
  inline synchronous `<head>` script.
- **New top-level destinations** (route stubs that compose into
  later phases):
  - `GET /app/rules` — watchdog-rule home with empty state and
    "what's coming" panel pointing at P4.
  - `GET /app/history` — unified log feed; v0.3.0 implementation
    renders the existing audit data; expanded into watchdog
    events + power events + schedule fires + notification sends
    in P6.
  - `GET /app/settings` — settings parent with tab strip linking
    System / Network / Authentication / Users / Invitations /
    Firmware / Theme / Profile sub-pages. New stub pages for
    System / Network / Authentication explicitly mark themselves
    "Coming in P5/P6".
- **Component library skeleton** at `templates/_components/`
  with `empty_state.html`, `error_state.html`, `settings_tabs.html`
  partials. Other phases build on these.
- **Auto-derived `active` slot** on every page from the URL prefix
  via `_ctx()` — so the nav highlights the right item without
  per-blueprint plumbing.
- **WCAG 2.5.5 touch targets**: minimum 44 × 44 px on every
  primary button on mobile.
- **Visible focus rings** on every interactive element.
- **`viewport-fit=cover`** + `safe-area-inset-bottom` so the
  bottom-tab bar doesn't collide with iOS home indicators.

### Fixed — pre-existing responsive failures

The seven mobile-overflow failures in `tests/qa/test_responsive.py`
at 375 px viewport (login, dashboard, devices, events, audit,
users) are addressed by the mobile-first stylesheet rewrite. They
were systemic CSS issues, not page-specific bugs. Running the
suite against the new shell confirms the contracts.

### Notes

- All existing URLs continue to resolve. No bookmarks broken.
- All existing endpoint names preserved (`admin_ui.*`,
  `admin_api.*`) so every `url_for(...)` in the codebase
  resolves. Templates were not touched outside `layout.html`.
- The `dashboard.html` template continues to render at `/app/`
  in v0.3.0 — its restructure into a true Inbox / Status feed
  is Phase 2 (P2) of the redesign plan.
- `/app/audit` continues to serve its current page; in P6 it
  redirects to `/app/history`.
- No schema change. No new env vars. No backend behaviour change.

### Changelog of design intent

This is the foundation phase that the brief calls Phase 1 — design
system, layout, navigation. P2 (Status feed + device list/detail
restructure), P3 (power-controls + safety + lockout flag), P4
(watchdog-rule builder), P5 (RBAC + auth foundation), P6 (history
+ notifications + settings), and P7 (polish) ship in subsequent
versions per `docs/webui-redesign-plan.md` §9.

## [0.2.11] - 2026-05-09

### Added — strict CORS allowlist (R8-CORS of REMEDIATION-PLAN-2026-05)

- `/api/v1/*` now honours a strict origin allowlist for cross-origin
  browser requests. Operators opt in via the new
  `REBOOTER_CORS_ALLOWED_ORIGINS` env var (comma-separated exact
  origins like `https://app.example.com`).
- Default allowlist is **empty** — behaviour is unchanged for every
  existing deployment. The new setting is purely additive.
- When an `Origin` header matches an allowed entry, the response
  carries:
  - `Access-Control-Allow-Origin: <echoed-origin>`
  - `Access-Control-Allow-Credentials: true`
  - `Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS`
  - `Access-Control-Allow-Headers: Authorization, Content-Type, X-Requested-With`
  - `Access-Control-Max-Age: 600`
  - `Vary: Origin`
- `OPTIONS` preflight requests against `/api/v1/*` from an allowed
  origin return `204` with the same headers. Disallowed origins fall
  through to the route handler (which generally 404s preflight, the
  cleanest signal to a browser to abort the actual request).

### Why hand-rolled

- The policy is narrow (one URL prefix, exact-match allowlist,
  credentials-on, fixed method/header set). Adding Flask-CORS for
  this is more surface than we need.
- One file (`app/middleware/cors.py`) is easy to audit.

### Operational

- `docker-compose.yml` updated to forward `REBOOTER_CORS_ALLOWED_ORIGINS`
  from the host environment. Set it on a per-deployment basis when a
  mobile app or cross-origin SPA needs to consume the API.

## [0.2.10] - 2026-05-09

### Added — server-side session table (R7-shadow of REMEDIATION-PLAN-2026-05)

- New `user_sessions` table. Every UI cookie login + every JWT
  access/refresh issuance writes a row at the moment of issuance.
- JWT payloads now include a `jti` claim, tying each token to a
  session row. The cookie session also carries an `sid` (jti) value
  so a future enforce path can correlate the cookie back to its row.
- `revoke_all_tokens()` now bulk-revokes every active session row for
  the user (in addition to the existing `tokens_valid_after` bump).
- UI logout (`GET /app/logout`) and API logout (`POST /api/v1/auth/logout`)
  mark the cookie session row revoked so a leaked cookie can't be
  replayed once the enforce switch flips.

### Why "shadow mode"

This release **does NOT yet reject any request based on session
state**. It populates the table; the request authoriser still relies
on the existing `tokens_valid_after` cutoff. A future minor will
flip the enforce switch behind a `REBOOTER_SESSIONS_ENFORCE` setting
once the table has been observed live for at least one minor and
operator confidence is established.

### Closes (when enforce flips)

- BUG-005 (signed-cookie revocation gap). Today's "revoke everywhere"
  invalidates JWTs but leaves Flask signed cookies usable for up to
  31 days. Once enforce flips, the new session-row check rejects any
  cookie whose row was marked `revoked_at`, regardless of cookie
  expiry.

### Operational

- Idempotent table create via the existing boot-time
  `Base.metadata.create_all()` advisory-lock path. No manual
  migration required.
- The session-write path is best-effort: a DB write failure logs but
  does NOT block the login.

## [0.2.9] - 2026-05-09

### Added — per-record audit slice (R3 of REMEDIATION-PLAN-2026-05)

- Device-detail page (`/app/devices/<id>`) and group-detail page
  (`/app/groups/<id>`) now embed an "Audit history" section showing
  the last 25 audit events that target the record. The composite
  `ix_audit_target` index already in place on `audit_events` makes
  this query cheap.
- "Full audit history for this device/group →" link drops the
  operator into `/app/audit` pre-filtered by `target_type` +
  `target_id`.
- Admin-UI `/app/audit` handler now parses `target_id` from the query
  string (the API endpoint already supported it; the UI was missing
  the param).
- `get_device_detail()` and `get_group_detail()` services return a
  new `audit_history: [...]` field — same shape as `/api/v1/admin/audit`
  rows (id, at, actor_email_snapshot, action, target_type, target_id,
  details).

### Notes

- Purely additive read path. No schema change. No feature flag.
- Per-record audit for sites, deployments, and firmware releases is
  scheduled for a follow-up minor; the device + group surfaces are
  the highest-traffic operator surfaces and ship first.

## [0.2.8] - 2026-05-09

### Added — first-class QA-fixture isolation (R2 of REMEDIATION-PLAN-2026-05)

- New `is_qa_fixture: bool` column on `devices` (default `false`).
  Idempotent boot-time `ALTER TABLE ADD COLUMN IF NOT EXISTS` keeps
  existing instances upgrade-safe — no manual migration required.
- Device registration auto-detects QA fixtures by display-name /
  enrollment-token-hint / enrollment-token-note prefix
  (`QA `, `qa-`, `qa_`, `test-`, `playwright`). Tests can also send
  an explicit `qa_fixture: true` in the register payload to be
  unambiguous.
- Devices list page and admin API gain a `show_qa_fixtures` toggle.
  In v0.2.8 the **default is "show"** so operators see the new
  toggle without data disappearing under them; v0.2.9 will flip the
  default to "hide" with a one-time info banner.
- Device-list rows render a small `QA` badge next to the display
  name when `is_qa_fixture = true`, so operators can spot fixtures
  even with the toggle on.
- Admin-API device serialiser returns `is_qa_fixture` so any
  consumer (mobile app, hub helper) can apply its own filter.

### Notes for the QA team

- Existing v027 tests continue to pass without modification — every
  test that creates a device uses a `QA …` display-name prefix, so
  they get auto-tagged on register.
- New `tests/qa/test_v028_fixture_isolation.py` regression-locks the
  contract: every QA-suite-created device is flagged; the
  `?show_qa_fixtures=0` URL hides them; the badge renders.

## [0.2.7] - 2026-05-09

### Fixed — UI no longer conflates "never heartbeated" with "offline"

- Devices list and device detail now render three distinct heartbeat
  states instead of the binary online/offline split:
  - `online` — heartbeat received within the last 3 min
  - `offline` — has heartbeated in the past, but not recently
  - `never` — device row exists but no heartbeat has ever been received
    (newly enrolled, or firmware mis-configured before first contact)
- API: `/api/v1/admin/devices` device rows gain a new `heartbeat_state`
  field. The existing `online: bool` is preserved for backwards
  compatibility and is True only for the `online` state.
- Dashboard: new "never heartbeated" stat tile alongside online /
  offline counts. New `stats.devices_never_heartbeated` and
  `stats.devices_offline_with_history` fields on
  `/api/v1/admin/dashboard` (legacy `devices_offline` unchanged).
- Device detail page: the Heartbeat section, when `last_heartbeat_at IS
  NULL`, now surfaces a "never heartbeated" badge plus a hint to check
  the firmware's `central_base_url`. v0.2.6 rendered a muted "No
  heartbeats received yet" line that was easy to miss.

### Operational

- Purged 9 leftover QA-suite device fixtures that were polluting the
  production devices view (all `display_name LIKE 'QA %'` with NULL
  `last_heartbeat_at`). Real fleet plus two real devices preserved.

### Notes for the firmware team

- `dev_01KR5HV2PY7CY1CD9WMWM3W1KS` (`test-s31-01`) stopped heartbeating
  at 2026-05-09T05:18:53Z and is genuinely offline as of v0.2.7
  release; UI now correctly shows it as `offline` (it has heartbeat
  history), not `never`.

## [0.2.6] - 2026-05-09

### Refactor — admin blueprints split into `app/blueprints/admin/`

- The two oversized files `app/blueprints/admin_ui.py` (945 lines) and
  `app/blueprints/admin_api.py` (784 lines) are gone. Each admin
  feature now has its own module under `app/blueprints/admin/`
  (devices, groups, sites, firmware, users, invitations, audit,
  enrollment-tokens, unregistered, events, dashboard, profile,
  auth-ui, public-invite). Each module owns both the UI handlers and
  the JSON API handlers for its feature; largest is now ~310 lines.
- Endpoint URLs and view-function names are preserved exactly — no
  client (firmware, mobile, ops tooling) sees any change. All
  `url_for("admin_ui.<name>")` calls in templates continue to resolve.
- New living docs: `docs/architecture.md`, `docs/contributing.md`,
  `docs/refactor-log.md`. Old session logs archived under
  `docs/sessions/`.

### Notes

- No new runtime dependencies. No schema changes. No behaviour change.
- Verified by full QA pass against both URLs (www + www2 fallback)
  before tagging.

## [0.2.5] - 2026-05-09

### Added — mass-action confirmation gate + unregistered-heartbeat tracker

- **Mass-action gate** (`app/services/mass_action.py`): any group
  fan-out command or firmware deployment affecting >5 devices requires
  `confirmation_level="simple"`; >20 devices requires
  `confirmation_level="typed"` with `confirmation_typed_value` echoing
  the prompted verb. Server-side enforcement; UI populates the form
  fields via `static/js/mass_action.js`. Closes BUG-012.
- **Unregistered-heartbeat tracker**
  (`app/services/unregistered.py`): every `/api/v1/device/*` 401 is
  best-effort logged with claimed device_id, source IP, endpoint,
  user-agent, auth-present flag. Surfaces in the admin UI at
  `/app/unregistered-devices` and via the dashboard tile + nav badge.
  Closes BUG-013.
- `services/bootstrap.py::ensure_schema()` no longer short-circuits
  when `users` exists — `Base.metadata.create_all()` is idempotent and
  cheap, so we run it under an advisory lock on every container start
  (auto-creates new tables added in later releases).

## [0.2.4] - 2026-05-09

### Added — operator dashboard + self-service profile

- **Real dashboard** — replaces the sparse nav-link list with stat
  cards (devices total/online/offline, devices with pending commands,
  groups + sites, firmware releases, 24h event count) and a unified
  recent-activity feed merging admin actions, device events, and
  issued commands in chronological order.
- **`/app/me` self-service profile** — every authenticated user can
  edit their own display name, change their own password (verifies
  current password, 8-char minimum), and "sign out everywhere"
  (revoke all their own sessions + JWTs). Changing the password
  automatically signs the user out of every other session.
- Profile link added to nav, plus a "profile · sign out" hint in the
  dashboard top line.

## [0.2.3] - 2026-05-09

### Added — UI affordances for shipped APIs

- **Delete a device** (admin+) — danger-zone button on device detail.
  Cascades credentials, heartbeats, events, commands, group memberships,
  deployment assignments. Audit-logged.
- **Delete a group** (admin+) — danger-zone button on group detail.
  Cascades memberships; member devices kept.
- **Cancel a pending invitation** (admin+) — button per pending row.
- **Edit a user's display name** (super-admin) — inline form on /app/users.
- **Revoke all tokens for a user** (super-admin) — bumps
  `tokens_valid_after`. If the super-admin revokes their own tokens,
  this session is also ended.
- **Assign a device to a site** (admin+) — site dropdown on device-detail.

### Fixed

- **POST /app/groups + POST /app/sites returned 500 on duplicate name**.
  Now catches `DuplicateNameError`, re-renders the list page with a
  friendly inline error and HTTP 409.
- **/rebooter/favicon.ico, apple-touch-icon.png 404** — now aliased
  to the existing static favicon (browsers request these at the
  conventional root regardless of `<link rel="icon">`).
- **/rebooter/robots.txt 404** — now `User-agent: * / Disallow: /`.
- **Default Flask 404 / 403 pages** — replaced with branded
  `error.html`. JSON paths still get the envelope `{ ok:false,
  error:{ code:"not_found"|"forbidden", … } }`.

### Changed

- `device.updated` audit-log entry now records exactly which fields
  the operator changed.

## [0.2.2] - 2026-05-09

### Changed

- **Session idle timeout is now 2 days** (was 31 days, the Flask
  default). Cookie expiry rolls forward on every request, so active
  users stay signed in indefinitely; idle users get kicked after 2
  days of no activity. Tunable via the
  `REBOOTER_SESSION_IDLE_TIMEOUT_SECONDS` env var.

### Operational

- All QA test data (114 devices, 66 groups, 14 sites, 31 invitations,
  18 throwaway users, 126 enrollment tokens, all 72 audit events,
  2 leftover firmware blobs) purged from the live DB. Architect
  account and the fresh firmware-team enrollment token preserved.

## [0.2.1] - 2026-05-09

### Added

- **Fallback URL is live**: `https://www2.voipguru.org/rebooter/`
  serves the same API and admin UI as the primary
  `https://www.voipguru.org/rebooter/`. Firmware clients should
  configure both URLs (primary first) and fall back per
  `docs/DEVICE_INTEGRATION.md`.
- Until v0.3 ships node-2 with its own Postgres, www2 is a transparent
  HTTPS proxy to www1 — same backend, same data, dual front-doors.
  Firmware blobs are served directly from the shared NAS on either
  node, no extra hop.
- Full QA suite (86 tests) green against **both** URLs.

### Changed

- `tests/qa/test_v02_rbac_invites.py::test_invitation_mint_returns_redeem_url`
  no longer asserts that the invite redeem URL host matches the
  request host — the backend always emits the canonical primary
  public base URL, by design.

## [0.2.0] - 2026-05-09

### Added — RBAC, invites, audit

- **Roles** on `users.role`: `super_admin`, `admin`, `operator`, `viewer`.
  `operator` can issue commands but not manage firmware/users; `viewer`
  is read-only; `admin` does everything except role changes; `super_admin`
  does everything including user/role management.
- **Email-invite signup** — admins mint an invitation via the API/UI;
  invitee redeems at `/app/invite/<token>` to set up their account.
  Single-use token, 7-day TTL by default. SMTP via env vars
  `REBOOTER_SMTP_*` (lifted from the DevinGPT pattern); the admin sees
  a copy-able link if SMTP isn't configured.
- **Audit log** — `audit_events` table records every admin mutation
  (device patches, command issuance, firmware deploys, user/invite
  changes). Surfaced at `/app/audit` and `GET /api/v1/admin/audit`.
- **User management endpoints** — `GET /admin/users`,
  `POST /admin/users/<id>/role` (super-admin only),
  `POST /admin/users/<id>/deactivate`,
  `POST /admin/users/<id>/revoke-tokens`.
- **Server-side token revocation** — bumping `users.tokens_valid_after`
  on logout / deactivate / revoke invalidates every JWT and Flask
  session cookie issued before that timestamp. Closes BUG-005.

### Fixed (cheap polish from QA pass)

- BUG-009: shipped a placeholder `favicon.ico` so browsers stop
  404'ing the icon request.
- BUG-010: `PATCH /admin/devices/<id>` now rejects unknown fields with
  `validation_failed` (was previously silently ignored).
- BUG-011: empty/no-op PATCH no longer bumps `updated_at`.

### Changed

- All admin API endpoints are explicitly role-gated. Existing
  super-admin sessions keep working unchanged.

## [0.1.4] - 2026-05-09

### Fixed / hardened (quick-wins from the QA pass)

- **BUG-006:** added per-IP rate limiting (10/min, 30/hour) on
  `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, and the
  HTML `POST /app/login`. Hits over the limit now return 429
  `rate_limited`. Backed by Flask-Limiter, in-memory storage.
- **BUG-007:** `groups.name` and `sites.name` are now `UNIQUE`. Creating
  a duplicate returns 409 `name_conflict` with a friendly message.
- **BUG-008:** firmware uploads of 0-byte files are rejected with
  400 `validation_failed` ("uploaded firmware is empty (0 bytes)").

### Added

- `app/middleware/rate_limit.py` — Flask-Limiter integration with the
  envelope-shaped 429 handler.

## [0.1.3] - 2026-05-08

### Fixed

- **BUG-001 (high):** enrollment-token redemption race. Two simultaneous
  `POST /device/register` calls with the same `enrollment_token` could
  both succeed, creating two devices for one token. Now serialised via a
  Postgres row-level `SELECT ... FOR UPDATE` so the loser returns
  `enrollment_consumed` (409). Surfaced by `tests/qa/test_hardening_probes.py::test_concurrent_enrollment_redemption_only_succeeds_once`.
- **BUG-002 (high):** concurrent firmware upload of the same `(version, channel)`
  used to produce a `500 internal_error`. The IntegrityError is now caught
  and translated to a clean `400 validation_failed` ("firmware …
  already exists") and the blob from the losing upload is cleaned up.
- **BUG-003 (medium):** `GET /api/v1/admin/devices/` (trailing slash) returned
  404 because Flask 3 defaults to `strict_slashes=True`. We now set
  `app.url_map.strict_slashes = False` so trailing slashes match.

## [0.1.2] - 2026-05-08

### Changed

- Login accepts either the full email or just the local-part (e.g.
  `dblagbro` works in addition to `dblagbro@gmail.com`) when there is no
  ambiguity. Login form input is now `type="text"` so browsers stop
  rejecting bare usernames as "not a valid email".

## [0.1.1] - 2026-05-08

### Added

- `users.is_super_admin` boolean column. The bootstrap admin is now marked
  as super admin / architect.
- `GET /api/v1/auth/me` now returns `is_super_admin`.
- Dashboard surfaces a "super admin · architect" badge for the architect
  account.

### Changed

- The startup bootstrap step now reconciles the bootstrap admin's password
  and elevation flags on every boot from `REBOOTER_BOOTSTRAP_ADMIN_*` env
  vars, instead of only inserting on first run. Rotating the env var is
  now sufficient to rotate the architect password.

## [0.1.0] - 2026-05-08

### Added

- Initial scaffold: Flask app, Postgres sibling, nginx routing under `/rebooter/`.
- Device API: register, heartbeat, command poll, command result, events upload, firmware check.
- Admin API: device list/detail/update, groups, group commands, firmware releases, firmware deployments, events query, sites CRUD.
- Admin web UI under `/rebooter/app/` (Jinja-rendered): dashboard, devices, device detail, enrollment tokens, groups, group detail, sites, firmware, events.
- Single-use enrollment tokens, admin-issued.
- Firmware binaries served directly by nginx from RAID6 volume; SHA-256 verified on upload.
- Per-device firmware assignments materialised from group/site/all_devices deployments; later deployments supersede pending ones.
- APScheduler in-process job: command expiry sweep every 30 s (single-worker via Postgres advisory lock).
- Locked v0.1 command payload schemas for `set_mode` and `apply_config` (agreed with firmware/design team 2026-05-09); malformed requests are rejected with `validation_failed`.
