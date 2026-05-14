# Hub Power + Recovery Alignment Plan - 2026-05-14

## Purpose

This note turns the current repo reality into an execution-ready hub-side
plan after the 2026-05-14 review of:

- the last 5+ shipped hub sprints (`v0.5.12` through `v0.5.23`)
- current code surface
- current backlog/docs
- latest firmware-side rollout and recovery status

It is intentionally biased toward the next operator-visible work needed to
align the hub with the now-real firmware state:

- firmware line: `0.1.18-dev-central-safe`
- fleet rollout: largely complete
- safe fallback / recovery: in place on firmware side
- protected config backup / restore: in place on firmware side
- live auto-rebind: in place and bench-verified
- current-firmware display in device web UI: in place on firmware side

The plan below assumes we are planning hub-side work only unless a step
explicitly calls out firmware coordination.

---

## Current baseline

## Already shipped on the hub

These are the main relevant building blocks we should build on, not
re-design:

- `v0.5.12`
  - B16 first slice: power-sample ingest endpoint + `DevicePowerSample`
  - B23: operator-meaningful central status chips
  - B24: desired-name push on ordinary rename
  - deployment completion on heartbeat
- `v0.5.14`
  - inline device on/off toggle on devices list
- `v0.5.17`
  - Roku integration + `roku_app_active`
- `v0.5.19`
  - rules edit flow + chips + richer event details + target picker
- `v0.5.20`
  - long-poll `/api/v1/device/commands`
- `v0.5.22`
  - desired-config blob + per-device drift detection + push-on-restore path
- `v0.5.23`
  - Home Assistant + Weather + iCal integrations

## Latest important reality

- Power data transport/storage exists on the hub, but the operator-facing
  power analytics UX does not.
- Desired-config drift exists on the device detail page, but not at
  fleet/status level.
- Status truth improved, but it still cannot represent the device-side
  recovery/rebind states now visible in firmware.
- Integrations expanded faster than the rules authoring/model surface.
- Docs/backlog are behind repo reality in multiple places.

---

## Planning principles

1. Prefer operator-visible surfaces over more hidden plumbing where the
   plumbing already exists.
2. Reuse shipped surfaces before adding new IA:
   - device detail
   - devices list
   - status page
   - settings/integrations
   - rules
3. Keep hub truth and firmware truth explicitly separate when they differ.
4. Do not block the whole roadmap on power analytics perfection; ship the
   lowest-friction useful slice first.
5. Any work that changes status semantics must come with QA coverage and
   a doc update in the same ship.

---

## Priority order

1. Power-monitoring UI
2. Rules / integrations alignment
3. Recovery / status truth
4. Desired-config drift visibility
5. Docs / backlog cleanup
6. Site/home profile + claim-assist/export groundwork

The order above is deliberate:

- power UI has the biggest operator-visible gap relative to already-shipped
  ingest
- rules/integrations alignment closes a live product inconsistency in the
  current repo
- recovery/status truth should build on the cleaned-up rules/integration and
  device-status surfaces rather than racing them
- drift visibility becomes much more useful once the status model can express
  recovery and rebind states correctly
- doc cleanup should happen as each ship lands, with one explicit cleanup pass
  at the end of this sequence
- site/home profile and claim-assist groundwork is still important, but it is
  lower-priority than the operator-facing telemetry, recovery, and drift gaps
  already exposed by current firmware and hub work

---

## 1. Power-monitoring UI

## Goal

Turn the shipped B16 ingest/storage slice into the operator-facing power
experience that was promised in the design/backlog:

- fleet-wide `/app/power`
- device-detail power analytics tab/section
- live last-sample card
- rollups/charts/tables

## Why this is first

- the hub already accepts and stores power samples
- firmware-side power/recovery work is now mature enough that operators need
  visibility, not just transport
- this item is mostly hub-local and does not require the status-model changes
  in item 3 to start delivering value

## Phase 1A - Query + live-sample surface

### Deliverables

- new power-query service module for:
  - latest sample by device
  - recent raw-series window by device
  - basic fleet summary for last 24h / 7d / 30d
- device detail:
  - convert the current `#power` section from "relay controls only" into
    "Power control + power telemetry"
  - add a live last-sample card with:
    - sampled time
    - watts
    - volts
    - current
    - power factor if present
    - frequency if present
    - RSSI if present
- devices list:
  - optional compact chip/column for "latest W" when a recent sample exists
  - keep it low-noise; do not attempt charting here

### Acceptance criteria

- operators can see whether a device is producing usable power telemetry
- no charting yet, but the latest sample is obvious and timestamped
- devices with no power samples render a clear empty state, not a broken card

### Dependencies

- none beyond shipped B16 ingest

### Suggested ship

- `v0.5.24`

## Phase 1B - Fleet `/app/power`

### Deliverables

- new `/app/power` page
- default ranges:
  - last 24h
  - last 7d
  - last 30d
- views:
  - sortable device table by kWh / avg W / peak W / sample freshness
  - top-N "largest consumers"
  - recent-sample health indicator so operators can distinguish "quiet device"
    from "no data"

### Acceptance criteria

- operators can answer "which devices are drawing the most power lately?"
- page remains useful even before rollups exist, using bounded raw-window
  queries for the initial ranges

### Dependencies

- Phase 1A query service

### Suggested ship

- `v0.5.25`

## Phase 1C - Rollups + charts

### Deliverables

- add rollup schema/job layer promised by B16:
  - hourly rollups
  - daily rollups
- device detail:
  - 24h chart
  - 7d table/summary
- `/app/power`:
  - by-device history chart
  - sample freshness / data completeness cues

### Acceptance criteria

- 24h chart loads fast on a normal fleet page load
- fleet-wide page does not depend on scanning raw samples for all historical
  ranges
- rollup backfill is safe and resumable

### Dependencies

- Phase 1A and 1B

### Suggested ship

- `v0.5.26`

## Phase 1D - Power rules hook points

### Deliverables

- define rule-facing derived predicates we may want next:
  - power is zero while relay_on=true
  - sustained phantom load
  - abnormal spike / abnormal drop
- do not fully implement them yet unless item 2 is already complete; this
  phase is mostly contract prep

### Acceptance criteria

- no hidden data-model surprises when we later add power-based probes/actions

### Dependencies

- item 2 must have normalized rule/integration contracts first

---

## 2. Rules / integrations alignment

## Goal

Make the shipped integration sources actually line up with the rule model,
validation, editing surface, and tests.

## Why this is second

This is the cleanest "repo says shipped, product path still inconsistent"
problem in the current codebase.

The live mismatch today is:

- runtime probe support exists for:
  - `roku_app_active`
  - `ha_state_is`
  - `weather_alert_active`
  - `ical_event_active`
- but the canonical rule kind/model/UI path still trails that expansion

## Phase 2A - Contract normalization

### Deliverables

- unify probe-kind definitions in one canonical place
- extend model/service validation to include all shipped probe kinds
- verify create, update, edit, render, and probe-now paths use the same
  canonical set

### Acceptance criteria

- a rule using any shipped integration probe can be:
  - created
  - edited
  - validated
  - rendered
  - executed by `probe_now`
  - executed by runtime

### Dependencies

- none

### Suggested ship

- `v0.5.27`

## Phase 2B - Rules UI support for shipped probes

### Deliverables

- extend rules create/edit UI to support:
  - Roku source + app name
  - HA source + entity id + expected state
  - Weather source + severity/event filters
  - iCal source + summary filter
- preserve JSON editor as escape hatch
- add source pickers that only show relevant integration sources

### Acceptance criteria

- an operator does not need raw JSON to use the shipped integration probes
- stale/missing-source behavior is clearly rendered in the rules UI

### Dependencies

- Phase 2A

### Suggested ship

- `v0.5.28`

## Phase 2C - Events, diagnostics, and guardrails

### Deliverables

- richer per-probe event rendering for the non-Roku integration probes
- clear stale-sample diagnostics in rule event logs
- integration-source health cues on Settings -> Integrations:
  - latest sample age
  - last error
  - sample summary that matches what the rule engine actually reads

### Acceptance criteria

- when a rule fails because the source is stale, operators can see that quickly
- integration troubleshooting no longer requires reading code or raw payloads

### Dependencies

- Phase 2B preferred, but some diagnostics can ship earlier if needed

### Suggested ship

- `v0.5.29`

---

## 3. Recovery / status truth

## Goal

Teach the hub to represent the device-side recovery and rebind states that
now exist in firmware, rather than collapsing them into generic stale/offline
language.

## Why this is third

- B23 already improved central status truth
- latest firmware work made new real states matter:
  - recovery mode
  - last-known-good restored
  - central disabled locally
  - registered without token / rebind needed
- this work is more contract-sensitive than item 1 and should build on the
  cleaned-up operator surfaces from items 1 and 2

## Motivating examples

- `.69` class: device is locally healthy/reachable enough for local admin or
  protected-backup work, but central is disabled on-device. This must not read
  like generic offline failure, because the correct operator action is very
  different from transport debugging or bad-OTA recovery.
- `.225` class: device is locally alive and centrally configured, but hub
  heartbeat/transport truth is stale. This is not the same problem as `.69`,
  and the hub should stop collapsing them into adjacent-looking red states.

## Phase 3A - Heartbeat/status contract expansion

### Deliverables

- agree and document additional device->hub status fields, ideally through
  heartbeat and only secondarily through events:
  - `central_enabled`
  - `central_registered`
  - `central_state`
  - `recovery_mode`
  - `last_known_good_restored`
  - optional `current_firmware` if distinct from `firmware_version`
  - optional `safe_fallback_reason`
- persist the new fields in hub-side device/heartbeat storage

### Acceptance criteria

- the hub can distinguish:
  - truly unreachable
  - reachable locally but central transport stale
  - central disabled on-device
  - recovery mode
  - rebind-needed / token-loss state

### Dependencies

- firmware coordination required for field contract if not already present

### Suggested ship

- `v0.5.30`

## Phase 3B - Device list/detail/status rendering

### Deliverables

- extend `central_status` derivation to incorporate the new fields
- add explicit operator-facing states/chips, for example:
  - `central_disabled_on_device`
  - `recovery_mode`
  - `rebind_needed`
  - `locally_alive_hub_stale`
- device detail:
  - recovery card
  - last known recovery/rebind outcome
  - distinguish hub belief from device-reported truth
- status page:
  - attention items for devices in recovery or rebind-needed states

### Acceptance criteria

- a `.69`-type device is not lumped together with a `.225`-type device
- operators can tell whether the fix belongs on LAN/device side, hub side, or
  firmware rollout side

### Dependencies

- Phase 3A

### Suggested ship

- `v0.5.31`

## Phase 3C - Recovery operator actions

### Deliverables

- add low-risk admin diagnostics/actions where appropriate:
  - rebind history or last auto-rebind timestamp
  - last protected-backup restore status if device reports it
  - explicit "push desired config after rebind" affordance if drift exists
- keep actions narrow; do not create a giant "recovery console"

### Acceptance criteria

- common post-recovery operator steps are visible and auditable

### Dependencies

- item 4 becomes much more useful here

---

## 4. Desired-config drift visibility

## Goal

Promote desired-config from a per-device editor into a fleet-level recovery
and re-enrollment visibility tool.

## Why this is fourth

The core B21 machinery exists already. The missing value is not more hidden
storage; it is surfacing drift where operators actually triage the fleet.

## Phase 4A - Fleet visibility

### Deliverables

- devices list:
  - drift chip/badge
  - last reported config freshness cue
  - last desired-config push age
- status page:
  - attention item for drifted centrally-managed devices
  - separate attention item when desired config exists but no
    `last_reported_config` has ever arrived

### Acceptance criteria

- operators can identify drift without opening every device detail page
- recovery/re-enroll incidents surface drift automatically

### Dependencies

- item 3 status expansion strongly preferred

### Suggested ship

- `v0.5.32`

## Phase 4B - Recovery-aware drift actions

### Deliverables

- when a device rebinds or exits recovery, surface:
  - whether desired config is in sync
  - whether a manual push is recommended
- optional guided action:
  - "push desired config now"
  - "compare desired vs reported"

### Acceptance criteria

- the operator path after recovery is explicit, not tribal knowledge

### Dependencies

- item 3

### Suggested ship

- `v0.5.33`

## Phase 4C - Scope and schema cleanup

### Deliverables

- reconcile hub-side allowed desired-config keys with the latest firmware-owned
  schema document
- add version-gating hints where fields are not universally supported
- decide whether the hub should continue accepting pass-through keys not yet
  clearly documented in the firmware contract

### Acceptance criteria

- desired-config no longer drifts into "hub accepts it, docs do not, firmware
  maybe ignores it" territory

### Dependencies

- firmware schema confirmation required

---

## 5. Docs / backlog cleanup

## Goal

Leave the repo in a state where the next person reads current truth rather
than archaeology.

## Why this is fifth

Every item above should update its own docs when it lands. This final bucket
is the explicit cleanup pass for stale top-level planning artifacts.

## Phase 5A - Rolling updates during each ship

Every ship in items 1-4 should update, at minimum:

- `CHANGELOG.md`
- any touched API/docs surface
- a targeted note if the ship changes operator semantics

This is not optional cleanup later.

## Phase 5B - Explicit stale-doc pass

### Deliverables

- refresh `docs/BACKLOG.md`
  - mark B16 ingest shipped / UI remaining
  - mark B21 core shipped / fleet visibility remaining
  - reflect current B17 adjacent integration status
- refresh `docs/API.md`
  - current endpoint contracts and version references
- refresh B16 planning doc status to reflect shipped ingest
- refresh integrations copy so it no longer describes HA/Weather/iCal as only
  roadmap
- reconcile the hub-side desired-config contract with the firmware-owned schema
  doc after the safe-fallback and protected-backup work:
  - allowed top-level keys
  - version/support expectations
  - any recovery/re-enroll assumptions now implied by protected backup,
    auto-rebind, and safe-fallback behavior
- add one concise cross-team note summarizing the locked hub/firmware recovery
  and power-monitoring alignment contract

### Acceptance criteria

- backlog items match repo reality
- docs stop implying features are merely planned when code shipped them
- docs stop implying features are shipped end-to-end when only partial slices
  landed

### Dependencies

- best done after at least items 1-4 Phase A slices land, but can start earlier
  for obvious corrections

### Suggested ship

- `v0.5.34`

---

## Recommended ship sequence

## Wave 1 - close the largest operator-visible gaps

1. `v0.5.24` - power live-sample/query surface
2. `v0.5.25` - `/app/power` first fleet view
3. `v0.5.27` - rules/integrations contract normalization
4. `v0.5.28` - rules UI support for shipped integration probes

## Wave 2 - upgrade truth surfaces

5. `v0.5.30` - recovery/status contract expansion
6. `v0.5.31` - device list/detail/status rendering for recovery truth
7. `v0.5.32` - fleet-level desired-config drift visibility

## Wave 3 - deepen the operator workflow

8. `v0.5.26` - rollups + charts
9. `v0.5.29` - integration diagnostics/event detail
10. `v0.5.33` - recovery-aware desired-config actions
11. `v0.5.34` - explicit stale-doc/backlog cleanup pass

## 6. Site/home profile + claim-assist/export groundwork

## Goal

Preserve a lower-priority but explicit lane for the broader B16-adjacent
operator workflow around household/site context, service-provider context, and
exportable incident material.

This is not first-wave power analytics work, but it belongs in the roadmap so
it does not disappear between power-monitoring and recovery work.

## Scope

Create groundwork for a future operator profile surface that can hold:

- site/home identity
  - address
  - contact name
  - phone/email
- provider context
  - ISP / utility / service providers
  - account or support-reference notes
- incident-packet export groundwork
  - selected device status snapshot
  - power summary
  - recent events
  - recovery/rebind state
  - contact/provider metadata

## Why this is separate and lower-priority

- it depends on the truth surfaces from items 1, 3, and 4 being trustworthy
- it is valuable for support/claims/export workflows, but it does not unblock
  the immediate operator gaps revealed by the latest firmware and fleet work
- it should start as schema/profile groundwork, not a full claims product

## Deliverables

- define minimal data model and ownership boundaries for:
  - site/home profile
  - provider/contact metadata
  - incident packet export inputs
- identify where this should live in IA:
  - likely site/settings surface, not device detail
- capture what exportable packet fields become available only after items 1-4
  land

## Acceptance criteria

- the backlog has an explicit lane for this work
- future support/export work can begin without reopening the entire B16/recovery
  planning discussion

## Dependencies

- item 1 power summaries
- item 3 status/recovery truth
- item 4 desired-config drift and rebind visibility

## Suggested timing

- planning/schema groundwork can happen in parallel with late Wave 2
- operator-facing UI/export should wait until after `v0.5.34`

---

This ordering intentionally lets us ship useful power UI before waiting on the
full recovery-status contract, while still forcing the rules/integrations
alignment work ahead of any power-based rule ambitions.

The recommended sequence does not materially change after the additions above.
The new lane is intentionally lower-priority and downstream of the truth and
telemetry work, so it should not displace the primary sequence.

---

## Testing and QA expectations

Each ship above should carry the smallest focused tests that prove the user
story, not just the helper function:

- power UI:
  - ingest -> query -> render
  - empty-state rendering
  - range aggregation correctness
- rules/integrations:
  - create/edit/probe-now/runtime for every shipped probe kind
  - stale-sample failure behavior
- recovery/status truth:
  - central disabled vs offline vs transport stale vs recovery mode
  - rebind-needed path
- drift visibility:
  - list/status/device-detail consistency
  - push action visibility after recovery
- docs:
  - no tests, but same-ship review checklist

Wherever possible, prefer QA coverage that exercises the operator-visible page
or API contract, not only isolated helper logic.

---

## Risks and assumptions

## Risks

- The recovery/status truth work may stall if firmware-side heartbeat fields
  are not stabilized quickly.
- Desired-config semantics can stay fuzzy if the hub and firmware schema docs
  keep drifting apart.
- Power UI can accidentally overfit to synthetic or sparse data if we do not
  test against live-ish sample distributions.
- Rules/integrations alignment may reveal more than one source of truth for
  probe kinds, which can widen the change surface beyond the initial estimate.

## Assumptions

- `0.1.18-dev-central-safe` remains the active firmware baseline for near-term
  hub alignment work.
- firmware-side live auto-rebind, recovery, protected backup/restore, and
  device-web-UI current-firmware display are treated as real and not
  experimental
- hub-side power ingest contract does not need a breaking API change for the
  first UI wave
- we can stage recovery/status truth in additive form before attempting any
  broader device-status refactor

---

## Bottom line

The next hub roadmap should not start with more hidden plumbing. The repo
already has enough shipped substrate to make meaningful operator progress now.

The best near-term sequence is:

1. expose power data
2. align rules with the integrations we already claim to support
3. teach the hub to tell the truth about recovery/rebind states
4. surface desired-config drift where operators triage the fleet
5. clean up the docs so the codebase stops arguing with itself
