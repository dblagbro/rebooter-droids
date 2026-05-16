# Pause State & Resume Charter — 2026-05-15 (v0.5.68)

**This document overrides the priority view in `BACKLOG.md` and every
prior pause-state note.** Read it first on resume.

## Progress (updated 2026-05-15, post v0.5.68)

- **P-REG — DONE (v0.5.68, both hubs live).** Writing the end-to-end
  adoption test the suite never had immediately surfaced **two** bugs,
  both fixed: (1) the showstopper — RBAC P2 (v0.5.36) made
  `devices.site_id` NOT NULL but `adopt()` mints siteless tokens, so
  `/register` 500'd for *every* announce-adopted device for ~32
  versions; (2) a lost `/announce` response permanently stranded a
  device at adoption. See CHANGELOG `[0.5.68]`. All three fixes
  verified live on tmrwww01 + tmrwww02.
- **P-QA — first gate DONE.** `.github/workflows/ci.yml`: every push /
  PR builds the image, boots it on a throwaway Postgres, and runs the
  `-m ci` bucket (21 registration/device-API/heartbeat tests). First
  green run confirmed. The v0.5.36-class regression can no longer
  merge silently. *Remaining P-QA:* widen the gate — triage the other
  ~56 test files, add `tests/unit/` in-process tests. See
  `docs/test-plan.md` §"How to widen the gate".
- **P-UI — NOT STARTED.** Still the next charter item. See §P-UI below.

## Why this document exists

The previous pause-state memory said the *"buildable backlog is fully
exhausted — everything left needs a human decision or firmware."* That
was wrong, and saying it was a failure of judgment.

The operator's standing assessment of this project, in their words:
**failing registrations, a terrible UI, and poor QA.** None of those
three were touched by the recent v0.5.50→v0.5.67 work. That work — the
P0–P3 firmware-contract track, B17 integrations, RBAC, two refactor
rounds — was real, but it was *internal/tactical*. It did not move the
product on any of the operator's three named problems. Refactoring
`rules.py` while registrations fail is polishing the wrong thing.

So the backlog is **not** exhausted. The three problems below are the
charter. Do not resume by picking the next refactor target or the next
`Bxx` integration. Resume on P-REG.

## Where things stand (facts)

- **Live: v0.5.67 on both hubs** (www = tmrwww01, www2 = tmrwww02),
  verified `GET /api/v1/version` → `0.5.67` on both. Sync every 3s.
  GitHub + Docker Hub `:0.5.67`/`:latest` current.
- The recent arc shipped a lot of versions. Ship count is not the
  metric that matters here. Operator-visible product quality is.

---

## P-REG — Registration / adoption reliability  ⟵ RESUME HERE

**Operator symptom:** registrations fail. Concretely flagged this
session: device `192.168.1.69` was online on the LAN but never
appeared in the hub.

**Why the existing record cannot be trusted.** `docs/rca-2026-05-09-no-device-online.md`
concluded *"server-side healthy — no code change required; cause is
device-side."* That RCA pattern — verify a synthetic happy-path
round-trip, then attribute every real failure to firmware — is exactly
how a real hub-side bug stays hidden. The operator still reports
failures a week and ~30 versions later. **Treat the 2026-05-09
"server-side healthy" verdict as unproven.**

**The adoption path is large and under-tested as a path:**
`app/services/announcements.py` (18 KB) → `app/blueprints/admin/pending_adoption.py`
(12 KB) → `app/services/enrollment.py` (17 KB) → `app/blueprints/device_api.py`
(12 KB, `/device/register` + `/device/heartbeat`). ~60 KB of code.
Tests exist (`test_device_api.py`, `test_v0502_pending_adoption_count.py`,
`test_v027_heartbeat_state.py`, B20 dupe tests) but they are
version-snapshot unit tests — **there is no single end-to-end test
that drives announce → pending-adoption → adopt → token mint →
`/device/register` → first heartbeat → "online" as one flow.**

**Resume actions (in order):**
1. Reproduce a real adoption end-to-end against a live device (the
   operator has LAN devices; `.69` is now on firmware `0.1.29` per the
   firmware handoff and is a good candidate). Instrument every hop;
   find the *actual* break point. Do not start from "it's probably the
   firmware."
2. Check the unhappy paths specifically: stale enrollment token after
   the v0.3.4 bulk-delete wipe; 401 → re-enroll loop; MAC-dupe restore
   vs fresh (B20); a device that announces but whose announcement never
   surfaces on `/app/pending-adoption`.
3. Whatever the root cause, the fix is not done until an end-to-end
   adoption regression test exists and runs in CI (see P-QA).

This is a **correctness** problem and the operator's #1 named pain. It
outranks everything else in this document.

---

## P-QA — QA is not a real safety net  ⟵ SECOND

**Facts:** 59 test files, **all** under `tests/qa/`, named per release
(`test_v0535_rbac_shadow_skeleton.py`, …). That is a pile of
regression snapshots, not a coherent suite. **There is no CI** —
`.github/workflows/` does not exist; nothing runs the tests on push or
on deploy. `docs/qa-notes.md` has grown to ~105 K tokens of accreted
notes — it is unreadable as a test plan.

So today: a refactor or a deploy can break adoption and nothing
catches it. That is *why* "poor QA" and "failing registrations" are
the same problem wearing two hats — without CI, a registration fix
cannot be proven or kept fixed.

**Resume actions:**
1. Stand up GitHub Actions CI: `pytest` (the non-slow bucket) on every
   push + a build of the Docker image. This is small and unblocks
   everything else.
2. Write the end-to-end adoption test from P-REG and make CI run it.
3. Triage `qa-notes.md` into an actual `test-plan.md` with coverage
   gaps named; archive the raw note history.

P-QA is second because the P-REG fix needs it to be provable.

---

## P-UI — The UI redesign that has been planned five times

**Facts:** the repo carries `RFC-003-web-ui-redesign.md`,
`webui-redesign-plan.md`, `webui-redesign-requirements.md`,
`webui-redesign-research.md`, `redesign-continuation-plan.md`, and
`redesign-continuation-plan-v2.md` — **six** documents. The v2 plan's
Tier A (RBAC) shipped; Tiers B–F (auth surface, notifications,
settings UX, a11y/onboarding/command-palette) largely did not. And
"terrible UI" is a quality verdict that sits *above* that feature
list — it will not be fixed by ticking Tier B.

The failure mode here is over-planning: the redesign has been
re-planned instead of executed, and shipped piecemeal during firmware
bring-up (see `BACKLOG.md` B18's "honest note on why this was missed").

**Resume actions:**
1. **Do not write a seventh plan.** Do a concrete heuristic
   walkthrough of every page at desktop *and* mobile widths. Write the
   result as a flat, numbered defect list with screenshots — real
   observed problems, not aspirations.
2. Prioritize that defect list with the operator. Fix top-down in
   small ships, each verified in the browser.
3. Only then consider the unshipped v2-plan tiers, and only the ones
   that survive contact with the defect list.

P-UI is third only because it is the largest and needs the
walkthrough first — not because it is optional.

---

## The "full redesign + research" the operator asked for

The operator notes they previously asked for a full redesign and
research into how to improve the project. That research exists as
documents (the six UI docs above, the RFCs, the B16/B17 designs) but
was never executed as a *coherent program* — it got displaced by the
firmware-contract P0–P3 track. P-REG + P-QA + P-UI above **are** that
program, made concrete and ordered. Resuming on them is resuming the
redesign, not deferring it.

## Everything else drops below the line

These remain valid work but **must not** be picked up before P-REG /
P-QA / P-UI:

- B11 `apply_outbox_event()` create/update upsert + LWW (gated on the
  operator's sync-strategy call; `sync.enabled=false` today).
- P3b cross-modal query layer (gated on RFC-006 §9 schema review).
- P1.3 loaded-power validation (firmware-blocked).
- The named refactor targets (`settings.py` 596 LOC, `devices_ui.py`
  563 LOC, `device_power.py` 723 LOC). **These are done being a
  priority.** They are code hygiene, not product problems.
- B17 EPG channel-mapping UI, MQTT/SNMP operator setup, etc.

## Operator action items (unchanged, still open)

- Enable SNMP on the UniFi gear, then add an `snmp` source.
- Configure the MQTT source (Mosquitto on the HA host) + restart the
  container (subscriber list is read at start-up).
- Firmware: confirm exact power-sample row keys for estimated-current
  fields (note filed `docs/notes/2026-05-15-to-firmware-current-semantics.md`).

## Mechanics

- **Deploy** (when code changes): `docker build -t dblagbro/rebooter-droids:<ver> -t …:latest .`
  then per host stop/rm/`docker run` (full env-var set; tmrwww02 uses
  `-www2` data dirs + `PUBLIC_BASE_URL=https://www2.voipguru.org/rebooter`),
  one node at a time, verify `curl …/api/v1/version` between. Postgres
  `rebooter`/`rebooter`. Then `git push` + two `docker push`. No
  `Co-Authored-By: Claude` trailer.
- **Verify on resume:** `curl -sf https://www.voipguru.org/rebooter/api/v1/version`
  + www2 → both `0.5.67`.
- **Repo state:** the v0.5.65 refactor's deletion of the old
  `app/services/external_sensors.py` (superseded by the
  `external_sensors/` subpackage) and the session's firmware-handoff
  notes were committed alongside this charter.

## Resume order — one line

**P-REG** (reproduce + fix a real adoption failure) → **P-QA** (CI +
end-to-end adoption test so the fix holds) → **P-UI** (defect
walkthrough, then fix top-down). Do not start anywhere else.
