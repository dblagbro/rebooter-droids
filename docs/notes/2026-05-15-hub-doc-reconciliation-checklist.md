# Hub Doc Reconciliation Checklist

Date: 2026-05-15
Companion to: `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md`
Purpose: concrete, pick-up-and-do worklist to bring canonical docs back in sync
with repo reality at **v0.5.50**.

> This is bookkeeping, not feature work. None of it requires a deploy. Do it as
> a doc-only ship so the next person who reads the backlog is not misled.

---

## Why this exists

Between v0.5.34 and v0.5.50 the repo shipped 17 versions (full B1 RBAC rollout,
full B11 sync scaffold) while `docs/BACKLOG.md` stayed frozen at v0.5.33. The
backlog now claims B1 and B11 are open when both have shipped. Several design
notes also still carry pre-flight "do not implement" or wrong-hardware
statements. Until these are fixed, the docs actively mislead.

---

## Checklist

### 1. `docs/BACKLOG.md` — HIGHEST PRIORITY

- [ ] Update the header date line (currently `Last updated: 2026-05-14 PM
      (post v0.5.33)`) to current state (post v0.5.50).
- [ ] Move **B1 RBAC** out of "Truly open" → "Shipped / CLOSED." All 5 phases
      shipped: P1 v0.5.35, P2 v0.5.36, P3 v0.5.37, P4a v0.5.38, P4b
      v0.5.39–v0.5.43, P5 v0.5.44.
- [ ] Move **B11 multi-hub sync** out of "Truly open." Record it accurately:
      Phases 1–7 shipped v0.5.45–v0.5.50, **but** `apply_outbox_event()`
      create/update upsert + LWW is a stub — B11 is a scaffold, not converging
      sync. Add the "finish the applier before `sync.enabled=true`" debt item.
      (See plan note §5.1.)
- [ ] Resolve the **B16** internal inconsistency — it is marked closed
      (1A–1D, v0.5.26–v0.5.32) in the "Shipped" section but still reads as a
      future "research item" in the operator-decision section. Remove the stale
      research-item framing.
- [ ] Update the **Phase 3 heartbeat-contract** row — it still says "UNBLOCKED
      2026-05-14 evening." It is unblocked *and not started*; point it at plan
      note **P0**.
- [ ] Drop the "Operator-decision research items" charter block (B1 / B11 / B17
      remaining / B17 EPG) — B1 and B11 are done; B17 remaining + EPG are
      covered by plan note P2 / §3.

### 2. `docs/B16-power-analytics-design.md`

- [ ] Change the status header. It still reads
      *"Status | Draft (planning-only deliverable; do not implement until
      firmware-team replies …)"*. Phases 1A–1D have all shipped — restatus to
      "Implemented (Phase 1A–1D shipped v0.5.26–v0.5.32); see plan note for
      P1 follow-through."
- [ ] Confirm the CSE7766 chip reference is correct here (the chip-ID fix was
      applied to this doc; verify no stray HLW8032 remains).

### 3. `docs/redesign-continuation-plan-v2.md`

- [ ] Fix the chip ID — Tier F still says the Sonoff S31 has an **HLW8032**.
      It is a **CSE7766**. (~line 207.)

### 4. `docs/PROJECT-STATE-2026-05-09-FULL-SYNC.md`

- [ ] Either refresh it to current state or add a clear banner at the top:
      "Historical snapshot — 2026-05-09. Superseded; see
      `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md`." A banner is
      cheaper than a rewrite and stops it being mistaken for current truth.

### 5. `docs/notes/2026-05-15-p3-implementation-progress.md`

- [ ] Mark it DONE. It says "IN PROGRESS — Devices complete, 3 resource types
      remaining"; P3 shipped as **v0.5.37**. Its "Next Session Checklist" is
      complete. Add a one-line "Superseded — P3 shipped v0.5.37" header.

### 6. `docs/notes/2026-05-15-b1-rbac-design.md`

- [ ] Update the header — it says "P1+P2+P3+P4a SHIPPED." P4b (v0.5.39–v0.5.43)
      and P5 enforce-toggle (v0.5.44) also shipped. RBAC is fully done.

### 7. `README.md`

- [ ] Soften the www2 description. It currently states
      *"Secondary (www2) — Active-active multi-hub sync"* as fact. B11 is a
      scaffold; create/update entity state does not converge yet. Reword to
      reflect that sync is present but not yet a finished active-active path
      (or note `sync.enabled=false` by default).

### 8. Hub debt — version-length bug (low priority)

- [ ] The old long dev filename
      `rebooter-0.1.17-dev-central-safefallback-2026-05-14.bin` surfaced a
      version-length issue during firmware scan. Not a blocker; clean up as
      hub debt when convenient. (Flagged in
      `2026-05-14-to-rebooter-droids-status-sync-and-b16-alignment.md`.)

---

## Suggested sequencing

Items 1–3 are the misleading ones — do them first, as a single doc-only ship.
Items 4–7 are lower urgency and can ride along. Item 8 is unrelated cleanup.

When the doc-only ship lands, bump `CHANGELOG.md` with a "docs: reconcile
backlog/design notes to v0.5.50 reality" entry so the cleanup itself is on the
record.
