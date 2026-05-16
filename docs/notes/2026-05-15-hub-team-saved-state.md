# Hub Team — Saved State / Pickup Note

Date: 2026-05-15
Current live version: **v0.5.50**
Full detail: `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md`
Doc cleanup worklist: `docs/notes/2026-05-15-hub-doc-reconciliation-checklist.md`

> Pickup note. Nothing here is deployed. Read the plan note for rationale.

---

## What is agreed

- **Mission:** the fleet is now a real power/telemetry sensor network (live
  CSE7766 data on `.48`), not just reboot relays. v1 expands toward power +
  multimodal analytics.
- **v1 strategy:** prioritize **zero-hardware-cost** data sources (data the
  operator already has) ahead of hardware-heavy paths (Zigbee/SDR/BLE proxies).
- **Sequencing principle:** truth → depth → breadth → architecture.
- **B11 is a scaffold, not done** — create/update replication + LWW is a stub
  (`app/services/sync.py:187`). Do not advertise www2 as live active-active
  sync. Finish the applier before `sync.enabled` is ever set true.
- The repo is ahead of `docs/BACKLOG.md` (frozen at v0.5.33); docs need a
  reconciliation pass.

## Current priority order

- **P0** — Absorb firmware status/recovery/heartbeat contract (~v0.5.51–53).
- **P1** — Power data-path follow-through: JSON query API, data-quality
  surfacing, loaded-power validation (~v0.5.54–56).
- **P2** — Zero-hardware-cost sources: solar → router → managed-switch →
  deeper Home Assistant bridge (~v0.6.x).
- **P3** — Cross-modal schema decisions now, via RFC-006 (parallel to P0–P2).

Demoted but **not dropped:** A4 Enphase PLC link-quality, G2 time-sync,
E5 Theengs/BLE, SDR, Zigbee, Tesla — see plan note §7.

## Immediate next 5 actions

1. Refresh stale docs — work the reconciliation checklist; start with
   `BACKLOG.md` and the `B16-power-analytics-design.md` "do not implement"
   header.
2. Start **P0.1** — persist the richer heartbeat fields + Alembic migration
   (`app/services/heartbeats.py` currently consumes only `reported_config`).
3. Open **RFC-006 (multimodal ingest)** as a stub so P3 decisions have a home.
4. File the firmware asks — loaded-power test and G2 time-sync are on the
   critical path.
5. Log the **B11 applier debt** as a tracked item gated before `sync.enabled=true`.

## Blockers / asks out

- **Firmware:** loaded-power test (all data is no-load — blocks P1.3);
  invalid-frame characterization (~35% invalid on `.48`); ~24h capture;
  G2 time-sync measurement; freeze the heartbeat field contract.
- **Product:** define B11 "done"; confirm v1 multimodal scope; decide whether
  site-profile + claim-assist export is in v1.
- **Research:** explicit recommendations for D1/D2/G1/G3; narrow Enphase to
  7.0+ metered; SunSpec read-only; keep A4/G2/E5 as real deliverables.

## What not to forget

- B11 is not finished — the "complete" commit message overstates it.
- Firmware emits rich truth (recovery/central state); the hub still discards it.
  That is P0 and the highest-value unblocked work.
- Real CSE7766 data is **no-load only** so far — do not trust kWh/cost
  analytics until loaded-power data exists.
- Router/switch telemetry is **not** covered by B17 Layer 2 EPG (that is
  TV-guide data) — it needs its own research/design pass.
- Demoted exploratory items must not be silently dropped — each is parked with
  a reason.
- Keep doc updates in the same ship as the work, not as a later cleanup.
