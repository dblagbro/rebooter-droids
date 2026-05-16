# Hub Team — Status Sync & Execution Plan

Date: 2026-05-15
Author: hub/backend alignment pass
Current live version: **v0.5.50** (`app/version.py`, `pyproject.toml`)
Audience: rebooter-droids (hub/backend) team, plus firmware / product / research coordination

> This is an **alignment + planning** note. Nothing here is a deploy request.
> The execution waves below are sequenced intent, not a release that is being cut today.

---

## 1. How to use this note

1. Read §2 (mission) and §3 (where we actually stand) for context.
2. §4 is the completed-vs-stale-docs reconciliation — it explains why the
   backlog looks wrong.
3. §5 records two corrections to the record that must not be lost.
4. §6 is the execution plan: priorities **P0 → P3**, in order, with rationale.
5. §7 lists exploratory items that were **demoted but not dropped**, with reasons.
6. §8 collects the explicit asks for firmware / product / research.
7. §9 is the immediate next-action shortlist.

A companion checklist for the stale-doc cleanup lives in
`docs/notes/2026-05-15-hub-doc-reconciliation-checklist.md`.

---

## 2. Strategic mission — where the hub is going and why

Rebooter began as a **local-first network watchdog**: Sonoff S31 smart plugs
that detect a wedged router/modem/appliance and power-cycle it. The hub
(`rebooter-droids`) is the optional cloud-side companion — group commands,
firmware rollouts, fleet visibility. Devices keep working when the hub is
offline; central coordination is purely additive. That contract does not
change.

What **is** changing is what the fleet *is*. The S31 carries a real **CSE7766**
power-metering chip, and as of `0.1.25-dev-central-safe` that chip produces
**real telemetry**, not synthetic estimates. The moment the plugs measure real
volts/amps/watts, the fleet stops being only a set of reboot relays and becomes
a **passive power-and-telemetry sensor network** that happens to also reboot
things.

That unlocks the B16 / B16-multimodal direction:

- **B16 (power analytics):** turn per-device power data into energy analytics,
  anomaly detection, and operator-facing claims support — spoilage claims, ISP
  outage disputes, utility billing disputes.
- **B16-multimodal:** fuse plug power data with **other signals the user
  already has** — rooftop solar, router/switch telemetry, a Home Assistant
  install, BLE environmental sensors — to do cross-modal correlation
  (e.g. "freezer load dropped *and* the room BLE sensor warmed up").

### The v1 strategic constraint (accepted)

v1 prioritizes **zero-hardware-cost data sources** — data the operator already
has on site — *ahead of* speculative hardware-heavy paths (dedicated Zigbee
coordinators, SDR dongles, BLE proxy firmware). This is a value-per-dollar
ordering, not a value-per-technical-interest ordering. It is accepted as the
working direction and is reflected in the priority order below.

### Why the priorities are ordered the way they are

The plan is ordered **truth → depth → breadth → architecture**:

- **P0 first — be honest about each device.** Firmware *already* emits a rich
  status/recovery contract. The hub currently throws almost all of it away.
  This is the cheapest possible win (no new code on the firmware side, no
  research, no external dependency) and it fixes a real operator-safety gap:
  today the hub cannot tell "in recovery mode" from "offline."
- **P1 second — finish the one modality that is already real.** Power
  telemetry is live and the UI mostly shipped. Closing the data-path gaps
  (query API, data-quality surfacing, loaded-power readiness) makes the one
  working modality trustworthy before we add more.
- **P2 third — add breadth via the cheapest sources.** Solar, router, switch,
  and a deeper Home Assistant bridge — all zero-hardware-cost.
- **P3 in parallel — lock the cross-modal schema decisions now.** These are
  *decisions*, not builds. They are cheap to make today and expensive to
  retrofit after a second modality has been stored the wrong way.

This is also a dependency/cost ordering: P0 has zero external dependency, P1 is
follow-through on already-shipped UI, P2 needs new drivers plus some research,
P3 is an RFC rather than a feature.

---

## 3. Where the project actually stands

Surveyed against repo reality (code + `CHANGELOG.md` + `git log`), not against
the backlog doc.

### Shipped and real

| Area | State | Evidence |
|---|---|---|
| **B1 RBAC** | **Fully shipped — all 5 phases.** P1 v0.5.35, P2 v0.5.36, P3 v0.5.37, P4a v0.5.38, P4b v0.5.39–v0.5.43, P5 (enforce toggle) v0.5.44. | CHANGELOG, `git log` |
| **B16 power ingestion** | **Shipped.** `POST /api/v1/device/power-samples` accepts the **full CSE7766 field set** — `v_v, i_ma, p_w, s_va, pf, hz, energy_wh, source, source_flags, chip_type` + RF/quality fields. | `app/blueprints/device_api.py:301`, `app/services/events.py:70` |
| **B16 power UI** | **Shipped — Phases 1A–1D, v0.5.26–v0.5.32.** Device-detail power **telemetry** card (live last-sample, live/stale/none badges, 14-day sparkline) distinct from power **control**; fleet `/app/power` page (24h/7d/30d, kWh/cost, hogs table, stacked-bar chart); daily rollups; cost calc; CSV export; power-targeted watchdog probes (`power_above`/`power_below`). | `templates/device_detail.html:158`, `app/blueprints/admin/power.py`, `app/models/power_rollups.py` |
| **B17 Layer 1 + adjacent** | **Shipped.** Roku v0.5.17; Home Assistant + Weather + iCal v0.5.23. Four `external_sensor_sources` kinds live with probes. | `app/models/external_sensors.py:27`, `app/services/external_sensors.py:311` |
| **B21 desired-config / drift** | **Shipped.** v0.5.22; drift visibility v0.5.31. `reported_config` **is** consumed → `Device.last_reported_config`. | `app/services/device_config.py`, `app/services/heartbeats.py:63` |
| **Real CSE7766 telemetry** | **Live on `.48`** (`0.1.25-dev-central-safe`). Real line voltage ~119.5 V; no-load so current/watts/VA = 0. Reports valid/invalid frame counts (942 valid / 507 invalid in last snapshot). | `docs/notes/2026-05-15-cse7766-real-telemetry.md` |

### Shipped as scaffold only — see §5

| Area | State |
|---|---|
| **B11 multi-hub sync** | Commit `3dd7aa0` labels v0.5.50 "B11 complete." Phases 1–7 (outbox model, emission, replicator daemon, HMAC peer auth, sync settings UI) all landed code. **But** `apply_outbox_event()` only applies deletes/tombstones — create/update upsert + last-writer-wins is an explicit `# TODO` stub. Sync is a working *scaffold*, not a converging sync. Default `sync.enabled=false`. |

### Not started (genuinely open)

- **Heartbeat / status-recovery contract absorption** — firmware emits it; hub
  discards it. This is **P0** below.
- **B17 remaining integrations** — MQTT, Plex/Jellyfin webhooks, Solar
  (Enphase/SolarEdge), iOS Shortcuts, Google Calendar OAuth, `host_awake`.
  All design-only, zero code.
- **B17 Layer 2 EPG** — TV programming guide. Design-only, zero code.
  *Note:* EPG is **TV-guide data**, not network telemetry — router/switch
  telemetry is uncovered by any current B17 design.
- **JSON power query API** — all power query functions feed server-rendered
  pages only; there is no `GET` power-samples/rollups endpoint.
- **Cross-modal multimodal schema** — no RFC yet.

---

## 4. Completed vs. what the stale docs imply

The repo shipped **17 versions** (v0.5.34 → v0.5.50: full RBAC rollout + full
B11 sync scaffold) while `docs/BACKLOG.md` froze at v0.5.33. The result is a
backlog that is actively misleading.

| Item | BACKLOG.md still claims | Reality |
|---|---|---|
| **B1 RBAC** | "Truly open … heavy ≥1 sprint, operator-deferred"; P3 "next" | All 5 phases shipped (v0.5.35–v0.5.44) |
| **B11 sync** | "Largest item on board … gated on B1," unstarted | Phases 1–7 shipped (v0.5.45–v0.5.50) — but applier is a stub (§5) |
| **B16 power** | reads as a "research item" in one section | Ingestion + Phases 1A–1D shipped (v0.5.12, v0.5.26–v0.5.32) |
| **B17 integrations** | Layer 1 done; rest open | Accurate — Layer 1 + adjacent done, rest genuinely open |
| **B21 drift** | Shipped v0.5.22 | Accurate |
| **Phase 3 heartbeat contract** | "UNBLOCKED 2026-05-14 evening" | Still unblocked **and still not done** — this is P0 |

**Docs confirmed stale (need a refresh pass):**

- `docs/BACKLOG.md` — frozen at v0.5.33; B1/B11 status obsolete. *Highest priority.*
- `docs/B16-power-analytics-design.md` — header still says
  *"Draft (planning-only … do not implement until firmware-team replies)"*
  despite all of Phase 1 having shipped. Most misleading single line in the docs.
- `docs/redesign-continuation-plan-v2.md` — still says the S31 has an
  **HLW8032** chip; it is **CSE7766**.
- `docs/PROJECT-STATE-2026-05-09-FULL-SYNC.md` — far behind; self-describes as a
  2026-05-09 pause state.
- `docs/notes/2026-05-15-p3-implementation-progress.md` — says "IN PROGRESS";
  P3 shipped as v0.5.37.
- `docs/notes/2026-05-15-b1-rbac-design.md` — header says "P1+P2+P3+P4a SHIPPED";
  P4b and P5 also shipped.

The reconciliation worklist is broken out into the companion checklist note so
it can be picked up independently.

---

## 5. Corrections to the record — do not lose these

### 5.1 B11 is a scaffold, not a finished sync

`apply_outbox_event()` in `app/services/sync.py` (~line 178–189) handles
delete/tombstone events but, for create/update events, only logs
`"Would apply event …"` and returns `True` next to an explicit
`# TODO: Implement actual entity upsert with last-writer-wins`. There is **no
LWW conflict resolution**. Two real hubs would **not** converge non-deleted
entity state.

Also: only `device`, `site`, `group`, `user` are syncable; the design doc lists
~16 aggregates (watchdog rules, schedules, role bindings, runtime settings,
firmware releases, enrollment tokens, …) — none are wired.

**Recommended disposition:**
- Do **not** advertise B11 as done. The README already markets www2 as
  "active-active multi-hub sync" — that is currently aspirational.
- Treat **"finish the `apply_outbox_event()` upsert + LWW"** as a tracked debt
  item that must land **before `sync.enabled` is ever flipped true**. It is not
  inserted into P0–P3 because the product is not relying on sync today
  (`sync.enabled=false` by default), but it is the gate on B11 being real.
- Power data is **correctly excluded** from sync (high-volume single-writer
  firehose) — no action needed there.

### 5.2 The firmware baseline changed materially — plan from the new one

Plan against this baseline, not the older "synthetic only / firmware not ready"
assumptions:

- **Real CSE7766 telemetry** on `.48` (`0.1.25-dev-central-safe`): real line
  voltage, `power_source = steady`, valid/invalid frame counts exposed.
- **Richer heartbeat + `reported_config`** shipped from `0.1.19-dev-central-safe`
  onward (see `docs/notes/2026-05-14-heartbeat-expansion-and-reported-config-memo.md`).
- **Safer fallback / recovery**: recovery mode, last-known-good restore,
  consecutive-unhealthy-boot tracking, holdoff/cooldown.
- **Protected config backup/restore** exists device-side; live auto-rebind
  proven on the bench unit.

The single biggest mismatch this creates: **firmware emits a rich truth signal
and the hub discards almost all of it.** That is P0.

---

## 6. Execution plan — priorities in order

Version numbers are **indicative sequencing**, not committed releases.

### P0 — Absorb the firmware status / recovery / heartbeat contract  ·  (~v0.5.51–v0.5.53)

**Why first:** zero external dependency (firmware already shipped it), bounded,
and it closes a real operator-safety gap — the hub cannot currently distinguish
*recovery mode*, *central disabled*, *registered-but-unhealthy*, and *offline*.
The `.69` case is the concrete failure: healthy and upgraded locally, but
central-disabled, so the hub shows it as stale/offline and misleads the
operator. BACKLOG.md itself flags Phase 3 as "UNBLOCKED 2026-05-14 evening" —
it has simply not been done.

**P0.1 — Persist the richer heartbeat fields** *(~v0.5.51)*
- Today `app/services/heartbeats.py` + `DeviceHeartbeat` (`app/models/devices.py`)
  store only the legacy set (firmware_version, ip, mode, relay_on,
  wifi_connected, health_state, uptime, cycles). Everything else is dropped.
- Add storage + Alembic migration for: `recovery_mode`,
  `auto_recovery_triggered`, `last_known_good_restored`,
  `consecutive_unhealthy_boots`, `in_captive_portal`,
  `holdoff_remaining_seconds`, `cooldown_remaining_seconds`,
  `central_enabled`, `central_registered`, `central_state`,
  `central_device_id`, `central_heartbeat_age_seconds`,
  `power_analytics_enabled`, `power_chip_type`, `power_sample_rate_hz`,
  `power_batch_seconds`.
- `reported_config` is already consumed — keep it.
- Decide per field: hot column on `Device` (for current truth + filtering) vs.
  history row on `DeviceHeartbeat` (for timelines). Recovery/central-state
  truth wants both: a hot column for "what is it now" and history for "when did
  it flap."

**P0.2 — Render distinct device states in the UI** *(~v0.5.52)*
- Replace the online/offline collapse with explicit states:
  *central disabled · recovery mode · registered-but-unhealthy · transport
  stale · rebind-needed · never-heartbeated · healthy.*
- Devices list + device detail. This is the fix for the `.69` confusion.

**P0.3 — Recovery-aware drift + config-schema reconciliation** *(~v0.5.53)*
- **Phase 4B:** post-rebind "push desired config now" — trigger off
  `recovery_mode` / `last_known_good_restored` transitions.
- **Phase 4C:** reconcile `docs/firmware-apply-config-schema-v01.md` with
  `ALLOWED_DESIRED_CONFIG_KEYS` in `app/services/device_config.py`. Document
  which keys are *supported end-to-end in practice* vs. merely *accepted by
  schema* (`device_config.py` accepts pass-through `power.*` etc.; docs still
  say only `device_name` is truly exercised — close that gap).

**P0 exit criteria:** an operator can look at any device and see its true
recovery/central state; the hub stores enough history to show when a device
flapped into recovery; desired-config drift is computed against
`reported_config`, not inferred.

---

### P1 — Power telemetry / analytics data-path follow-through  ·  (~v0.5.54–v0.5.56)

**Why second:** the power *UI* gap the 2026-05-14 status note flagged is
essentially **closed** — telemetry card, `/app/power`, rollups, cost, CSV all
shipped. What remains is **trust and reach** for the one modality that is
already real. Finish the working modality before adding more.

**P1.1 — JSON power query API** *(~v0.5.54)*
- There is currently **no `GET` power API** — query functions in
  `app/services/device_power.py` feed only server-rendered admin pages.
- Add read-only, RBAC-scoped endpoints:
  `GET /api/v1/admin/devices/{id}/power-samples` (recent/windowed),
  `GET /api/v1/admin/devices/{id}/power-rollups`,
  `GET /api/v1/power/summary` (fleet 24h/7d/30d).
- This is also the seam the cross-modal query layer (P3) will reuse — design
  the response envelope with that in mind.

**P1.2 — Power data-quality surfacing** *(~v0.5.55)*
- Firmware exposes `power_valid_frame_count` / `power_invalid_frame_count` /
  `source` / `source_flags`. The `.48` snapshot showed **507 invalid of ~1449
  frames (~35%)** — UART/frame noise is a real operational signal.
- Ingest and surface valid/invalid counts; decode `source_flags`; badge each
  sample's `source` (`steady` real vs. synthetic fallback) so charts and
  rollups never silently average real and synthetic data together.

**P1.3 — Loaded-power readiness + interactive chart** *(~v0.5.56)*
- Every real sample seen so far is **no-load** (current/watts/VA = 0). Before
  cost/kWh analytics are trustworthy, verify ingest, rollups, cost calc, and UI
  all behave correctly with **non-zero** current/watts. This is gated on the
  firmware loaded-power test (§8).
- Add an interactive 24h time-series chart on device detail (today there is
  only a static SVG sparkline + fleet stacked-bar).

**P1 exit criteria:** power data is reachable programmatically, real-vs-synthetic
is never conflated, data quality is visible, and the analytics are validated
against real *loaded* readings.

---

### P2 — Zero-hardware-cost integration sources  ·  (~v0.6.x)

**Why third:** breadth, but only via sources that cost the operator nothing
extra. Ordered by *design-readiness × value*, consistent with the research
disposition (power first → solar second → existing-platform integrations next).

**P2.1 — Solar** *(first — design-ready)*
- New `external_sensor_sources` kind `solar`; poll driver; probes
  `solar_production_above` / `solar_production_below` mirroring the shipped
  `power_above`/`power_below`.
- **Enphase first pass: firmware 7.0+ metered gateways only.** Do not let
  pre-7.0 / non-metered coverage block the first driver.
- **SunSpec: read-only, minimal viable register/model set.** No inverter-control
  writes in the driver API surface, v1.x.
- Highest in P2 because it is a *direct power covariate* (load × generation =
  export) and B17 already has a design for it.

**P2.2 — Router telemetry** *(research/design pass first)*
- No existing pattern and B17 has **no design** for it (Layer 2 EPG is TV-guide
  data, not network telemetry). Needs a short research+design pass before code.
- Target signals: WAN up/down, throughput, error/retransmit counts, uptime —
  high-leverage covariates for the watchdog story and for power correlation.

**P2.3 — Managed-switch telemetry**
- Per-port link state, traffic, error counters (SNMP / vendor API). Same
  research-gate as router telemetry; can share an ingest pattern with it.

**P2.4 — Home Assistant bridge deepening**
- HA shipped only as a single-state `ha_state_is` probe. A fuller bridge can
  pull many already-modeled HA entities at near-zero marginal cost — elevate
  per the research disposition.

> Per the research feedback, the highest-leverage zero-hardware-cost covariates
> are Home Assistant + router + managed-switch telemetry. They are sequenced
> after solar **only** because solar has a finished design and a sharper v1
> value story; router/switch are research-gated, not lower-value.

---

### P3 — Cross-modal schema / architecture decisions to make NOW  ·  (RFC, parallel to P0–P2)

**Why now:** these are **decisions**, not builds. They are nearly free to make
today and very expensive to retrofit once a second modality has been persisted
the wrong way. Capture them in **RFC-006 (multimodal ingest)** and treat the
storage shape as *not settled* until this RFC has had a schema review.

Decisions to lock:

1. **Common ingest envelope** — `source_id`, `device_id`, `sampled_at`,
   `modality`, `quality` / `source_flags`, `metadata`.
2. **Modality-specific physical stores** — keep the **typed** power sample
   table (`DevicePowerSample`) fast and typed; add separate per-modality stores,
   or a JSONB payload behind the common envelope with modality-specific views.
   **Do not** build one giant sparse "one column per phenomenon" table.
3. **Cross-modal query layer is a first-class requirement, not an afterthought.**
   Preserve room for a materialized view / fast time-bucket path supporting:
   point-in-time multimodal lookup, windowed multimodal correlation, and
   change-detection across modalities. Not a v1.0 build — but a **schema-review
   gate** before the storage shape is considered final.
4. **Mixed transport, one normalized ingest layer** — keep **direct HTTPS
   ingest** for constrained plug firmware (no forced MQTT on the plugs). Use
   MQTT internally only where the source natively lives there (Zigbee2MQTT,
   rtl_433, possibly HA/BLE sidecars).
5. **Independent modality adapters** — each modality adapter is its own
   service/process; one source failing degrades only that modality; analytics
   fusion must operate on partial inputs.
6. **Time sync (G2) must be measured, not assumed** — the hub must not design
   tight-window / phase-locked multimodal analytics until firmware delivers a
   measured cross-device drift characterization (§8).

**P3 exit criteria:** RFC-006 exists, has had a schema review, and every P2+
source can be added without reshaping storage.

---

## 7. Exploratory items — demoted, NOT dropped

These were moved off the v1 critical path. Each is parked **with a reason** and
stays on the research track. None of these is being deleted.

| Item | Disposition | Why demoted (not dropped) |
|---|---|---|
| **A4 — Enphase PLC link-quality discovery** | Research deliverable, parked behind P2.1 solar. | Exploratory and not on the v1 critical path — but it must produce a real outcome: either a captured local/UI-backed endpoint + schema, **or** a documented "investigated, could not surface locally." Not allowed to silently fall out of scope. |
| **G2 — measured time-sync** | Firmware research task; **gates** P3 decision 6. | Demoted from "build" to "measure first" — but it is a first-class deliverable because it determines whether tight-window multimodal analytics are even realistic. See §8. |
| **E5 — Theengs / free BLE covariates** | Research track; below solar/router/switch for v1. | Elevated by research to *near* the HA/router/switch tier *if the operator already owns BLE sensors* — genuinely near-zero hardware cost. Still below v1 zero-cost sources because it needs an ESP32 BLE-proxy path, and BLE work must not destabilize 1 Hz power capture on ESP32 plugs (timing jitter / memory headroom must be measured first). Kept explicitly on the roadmap. |
| **F — SDR (rtl_433 utility-meter capture)** | Advanced / opt-in, post-v1. | Good research path, not v1 core. Product/legal stance must be settled before any normalization work. MQTT-internal transport already reserved for it in P3 decision 4. |
| **Zigbee (D1/D2 — coordinator + Zigbee2MQTT)** | Post-v1 unless effectively free. | Hardware-heavy (dedicated coordinator). Prior recommendation if pursued: SLZB-06MG24 coordinator + Zigbee2MQTT stack — pending the research team's explicit recommendation and 24h ops test. |
| **B — Tesla** | Feasibility only, not a blocker. | Local API access is eroding; if pursued, plan explicitly for **both** a local driver and a cloud-fallback driver — do not hide that behind a single "Tesla works" claim. |
| **H — Whisker Labs / Ting** | Complementary positioning only. | Not a competitor. We will not pretend plug hardware competes on arc-detection-grade sampling rates. |

---

## 8. Blockers & asks

### For the firmware team

1. **Loaded-power validation.** Every real CSE7766 sample so far is no-load
   (`current_ma`/`power_w`/`apparent_power_va` = 0). The hub cannot validate
   watts → kWh → cost against reality until firmware provides a **known-load**
   capture. *Blocks P1.3.*
2. **Invalid-frame characterization.** `.48` showed ~35% invalid frames
   (507/1449). Is that expected UART noise or a wiring/sampler issue? The hub
   will surface valid/invalid counts as a quality signal (P1.2) but needs
   firmware to say what "normal" looks like.
3. **~24h mixed-load capture.** Per the research disposition: capture ~24h of
   real mixed-load data, characterize noise/jitter, and confirm the *actual*
   atomic-snapshot behavior (not just the intended one). The hub's rollups and
   charts should be validated against this trace.
4. **G2 time-sync measurement.** Empirically measure cross-device timestamp
   drift / NTP convergence — ≥3 devices on a common LAN (one CSE7766 plug, one
   ESP32-C3 / alternate path, one Shelly path if available), reported vs. the
   hub over a meaningful run. *Gates P3 decision 6.*
5. **Heartbeat field contract freeze.** P0.1 builds persistence against
   `recovery_mode`, `central_state`, `consecutive_unhealthy_boots`, etc. —
   confirm the field names and enum values (`central_state` = `idle` / …) are
   stable so the hub is not chasing churn.
6. **`reported_config` field-support truth.** Confirm which `reported_config`
   keys are honored end-to-end vs. merely accepted, so P0.3 / Phase 4C can
   document supported keys accurately.

### For product

1. **B11 status honesty (§5.1).** Decide: is B11 acceptable as
   delete-only-converging, or must the `apply_outbox_event()` upsert + LWW be
   finished before B11 is called done and before `sync.enabled` can be true?
   Recommendation: finish the applier as a tracked debt item; meanwhile do not
   advertise www2 as live active-active sync.
2. **v1 multimodal scope confirmation.** Confirm the accepted ordering — power
   first → solar second → HA/router/switch next → Zigbee/BLE/SDR later. (This
   plan assumes it is accepted.)
3. **Site/home profile + claim-assist export.** The 2026-05-14 status note
   flagged a missing site/home profile model and a claim-assist/export workflow
   for spoilage / ISP / utility use cases. Confirm whether v1 needs this; if so
   it becomes a P1.5 / P2-adjacent item.

### For the research team

1. Keep **Priority 1** (Enphase, SunSpec) as the critical path; **elevate
   I1–I4** (Home Assistant / router / switch telemetry) above most BLE/SDR work.
2. Require **explicit recommendations** — not just captured tradeoffs — for
   **D1/D2** (Zigbee coordinator + stack), **G1** (event bus: "heterogeneous
   transports + normalized ingest" vs. "MQTT everywhere" — hub recommends the
   former), and **G3** (hub sizing, split into tiered recommendations:
   power-only / power+solar / full multimodal → entry / recommended / advanced).
3. Narrow the **Enphase** first pass to 7.0+ auth + metered gateways; lock
   **SunSpec** v1.x to read-only.
4. Treat **A4** (PLC link-quality), **G2** (timing), and **E5** (Theengs BLE) as
   real deliverables — they are demoted, not dropped (§7).
5. The next dev note must explicitly reason from the **changed firmware
   baseline**: real CSE7766 telemetry exists now — use real noise/jitter/load
   traces, not synthetic assumptions.

---

## 9. Immediate next actions

1. **Refresh the stale docs** — work the companion checklist
   (`2026-05-15-hub-doc-reconciliation-checklist.md`). Highest value:
   `BACKLOG.md` and the `B16-power-analytics-design.md` "Draft / do not
   implement" header.
2. **Start P0.1** — heartbeat-field persistence + Alembic migration. It is
   unblocked, bounded, and high operator value.
3. **Open RFC-006 (multimodal ingest)** as a stub now so P3 decisions have a
   home and the schema-review gate is on the record before P2.1 solar lands.
4. **File the firmware asks** in §8 — items 1 (loaded-power) and 4 (G2) are on
   the critical path for P1.3 and P3.
5. **Log the B11 applier debt** (§5.1) as a tracked item gated before any
   `sync.enabled=true`.

---

*Companion: `docs/notes/2026-05-15-hub-doc-reconciliation-checklist.md` —
concrete stale-doc cleanup worklist.*
