# RFC-006: Multimodal Ingest

| Field | Value |
|---|---|
| Status | **Draft** (P3 of the 2026-05-15 hub-team plan — "decisions to make NOW") |
| Authors | rebooter-droids backend/web team |
| Targets | rebooter-droids backend, Postgres schema, the external-sensor + power services |
| Supersedes | — |
| Superseded by | — |
| Companion | `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6 (P3), `docs/notes/2026-05-15-p2-router-switch-telemetry-design.md` |

> **This RFC locks decisions, not code.** Per the hub-team plan §6, the
> P3 cross-modal items are *decisions* that are cheap to make today and
> expensive to retrofit once a second (now third) modality has been
> persisted the wrong way. No schema migration lands until §9's
> schema-review gate is cleared.

---

## 1. Why now

As of v0.5.59 the hub ingests **three telemetry modalities**:

| Modality | Source | Storage today |
|---|---|---|
| **power** | S31 CSE7766 (`POST /api/v1/device/power-samples`) | `device_power_samples` — **typed table** + `device_power_rollups` |
| **network** | router/switch SNMP poll (v0.5.58) | `external_sensor_samples` — **generic JSON `payload`** |
| **solar / appliance-state / weather / calendar** | SolarEdge, Enphase, Home Assistant, NWS, iCal, Roku | `external_sensor_samples` — generic JSON `payload` |

The codebase has *already* forked into **two storage shapes** — a fast
typed table for the high-volume power firehose, and a generic
JSON-payload table for everything polled. That fork was not a mistake
(see Decision 2), but nothing yet unifies *querying* across them, and
the `modality` tag is applied inconsistently (`power` API responses and
the SNMP payload carry it; solar and HA payloads do not).

The B16-multimodal direction — fusing plug power with solar, network,
and HA signals for cross-modal correlation — needs a **common envelope**
and a **cross-modal query layer**. This RFC defines both before a fourth
modality lands and makes the divergence worse.

---

## 2. Current state — verified

- `device_power_samples`: typed columns (`v_v`, `i_ma`, `p_w`, …),
  `source`/`source_flags`, indexed `(device_id, channel_id, sampled_at)`.
  High volume (1 Hz batched). Correctly **excluded from B11 sync**.
- `external_sensor_samples`: `(id, source_id, sampled_at, payload JSON)`.
  Low volume (poll cadence 30–300 s). One row per poll; `payload` shape
  is kind-specific.
- `modality` tagging: present in the P1.1 power API envelope and the
  v0.5.58 SNMP poll payload; **absent** from solar/HA/weather/iCal/Roku
  payloads.
- No query path joins the two tables. Watchdog probes read one source
  at a time; there is no "what did every modality read at time T" call.

---

## 3. The six decisions

### Decision 1 — Common ingest envelope

**Every persisted sample, in either store, is describable by a common
envelope:**

```
{
  "source_ref":  "<device_id | external_sensor_source.id>",
  "modality":    "power | network | solar | appliance_state | weather | calendar | media",
  "sampled_at":  "<ISO-8601 UTC>",
  "quality":     { "source": "...", "flags": <int>, ... },   # modality-defined
  "metrics":     { ... },                                     # modality-defined
  "metadata":    { ... }                                      # optional, modality-defined
}
```

The envelope is a **read/query contract**, not a new table. Serializers
(`device_power._serialize_sample`, `external_sensors` poll payloads)
converge on emitting these keys. Concretely:

- `modality` becomes **mandatory** on every poll payload and every
  sample-serialization path. Backfill the missing ones (solar → `solar`,
  HA → `appliance_state`, weather → `weather`, iCal → `calendar`,
  Roku → `media`).
- `quality` generalizes the power `source`/`source_flags` data-quality
  work (P1.2) to all modalities — a network sample's `quality` can carry
  counter-reset flags; a solar sample's can carry `stale`/`vendor`.

### Decision 2 — Modality-specific physical stores (keep the fork)

**Ratified: the typed-vs-JSON fork stays. Do NOT build one giant sparse
"one column per phenomenon" table.**

- `device_power_samples` stays a **typed** table — it is the only
  high-rate firehose, typed columns keep aggregation fast, and B16
  analytics depend on it. Untouched.
- Polled, low-rate modalities stay in `external_sensor_samples` with a
  JSON `payload`. Adding a modality there is already zero-schema-change
  (proven by solar + SNMP).
- A future high-rate non-power modality (none on the roadmap) would get
  its **own typed table**, not a column bolted onto an existing one.

The common envelope (Decision 1) is what makes the fork invisible to
*query* — physical storage stays modality-appropriate.

### Decision 3 — Cross-modal query layer is first-class

A new read-only service module — proposed `app/services/multimodal.py`
— is the **single** cross-modal entry point. Required capabilities:

1. **Point-in-time lookup** — `readings_at(when, *, device_id=None)` →
   every modality's nearest sample to `when`.
2. **Windowed correlation** — `series(modality, ref, window)` returning
   the common-envelope shape, so a caller can line up power vs. solar
   vs. network over the same window.
3. **Change-detection** — "modality X moved > Δ within N s of modality Y
   moving" — the freezer-load-dropped-and-room-warmed case.

It reads `device_power_samples` and `external_sensor_samples` behind the
envelope; callers never touch the physical tables. P1.1's
`modality`-tagged JSON API envelope is the HTTP projection of this
layer — `app/blueprints/admin/power_api.py` is the template the future
`/api/v1/admin/multimodal/*` endpoints follow.

**Not a v1.0 build** — but the storage shape is **not considered final
until this query layer has had the §9 schema review.**

### Decision 4 — Mixed transport, one normalized ingest layer

- **Constrained plug firmware keeps direct HTTPS ingest.** No forced
  MQTT on the S31s — `POST /api/v1/device/power-samples` stays.
- **Polled sources stay pull-mode** over their native transport (HTTP
  JSON, SNMP) — the `external_sensors` poller.
- **MQTT is used internally only where a source natively lives there**
  (a future Zigbee2MQTT / rtl_433 sidecar). It is never imposed on
  sources that do not need it.
- Whatever the transport, every path normalizes into the Decision-1
  envelope before persistence — *one* normalized ingest contract, many
  transports.

### Decision 5 — Independent modality adapters

Each modality's poll/ingest path is **independently failing**:

- The `external_sensors` poller already isolates per-source errors
  (`poll_source` records `last_error`, the tick marches on). This
  property is **ratified and extended** — one modality's outage must
  degrade only that modality.
- The cross-modal query layer (Decision 3) **must operate on partial
  inputs** — `readings_at()` returns whatever modalities have data and
  marks the rest absent; it never fails because one modality is down.
- Analytics fusion treats every modality as optional.

### Decision 6 — Time sync must be measured, not assumed

Cross-modal correlation is only as trustworthy as the timestamps it
lines up. **The hub will not ship tight-window / phase-locked
multimodal analytics until cross-device clock drift is measured.**

- `sampled_at` is always **UTC, hub-normalized where the source is
  untrusted.** Plug firmware timestamps are device-clock; poll-sourced
  samples are stamped at hub poll time.
- Until the firmware team delivers the **G2 measured-drift
  characterization** (hub-team plan §8, firmware ask #4), the query
  layer exposes correlation only at **coarse windows (≥ 60 s buckets)**
  — never sub-second alignment.
- `metadata.clock_source` (`device` | `hub_poll`) records which clock
  stamped each sample, so a future tightening is possible without a
  migration.

---

## 4. Schema impact

**Minimal — the envelope is mostly a serialization contract:**

- **No change** to `device_power_samples` / `device_power_rollups`.
- **No change** to `external_sensor_samples` structure — `payload`
  already absorbs per-modality `metrics`/`quality`/`metadata`.
- The only persisted addition under consideration: a `modality` column
  on `external_sensor_sources` (denormalized from `kind`) so the
  cross-modal query layer can filter without a kind→modality map in
  code. **Deferred to the schema review** — a code-side
  `KIND_TO_MODALITY` map is the zero-migration alternative and is
  preferred for v1.

So RFC-006 is, by design, **near-zero migration** — its value is
locking the contract, not moving data.

---

## 5. Phasing

| Phase | Scope | Gate |
|---|---|---|
| **P3a** | Make `modality` mandatory + consistent on all poll payloads and serializers; add the `KIND_TO_MODALITY` map. Pure serialization. | none — safe now |
| **P3b** | `app/services/multimodal.py` — `readings_at()` + `series()` over the envelope. Read-only. | §9 schema review |
| **P3c** | `/api/v1/admin/multimodal/*` HTTP projection. | P3b shipped |
| **P3d** | Change-detection / correlation analytics. | G2 drift data (Decision 6) |

P3a is genuinely safe to ship in the next routine version. P3b+ wait on
the schema review.

---

## 6. Open questions

1. **Operator** — is cross-modal correlation a v1 product goal, or is
   per-modality monitoring (what ships today) sufficient for now? P3b–P3d
   are only worth building if cross-modal *analytics* are wanted.
2. **Firmware** — the G2 measured-drift characterization (hub-team plan
   §8 ask #4) gates Decision 6 / Phase P3d. No hub work proceeds on
   tight-window analytics until it lands.
3. **Schema review** — before P3b, one reviewer confirms the Decision-1
   envelope survives a hypothetical fourth modality (e.g. BLE
   environmental, SDR utility-meter) without reshaping. This is the §9
   gate.

---

## 7. Recommendation

**Adopt Decisions 1–6 as the locked contract.** Ship **P3a** (mandatory
consistent `modality` tagging + `KIND_TO_MODALITY`) in a routine version
— it is pure serialization, zero migration, and stops the tagging
inconsistency from spreading. Hold **P3b+** behind the schema review and
the operator's answer to open question #1.

The headline outcome: the typed-power / JSON-polled storage fork is
**ratified, not a debt** — and a common envelope + a single cross-modal
query module is the agreed shape for unifying them, with no large
migration required.

---

## 8. Decision log

| # | Decision | Resolution |
|---|---|---|
| 1 | Common ingest envelope | `source_ref` / `modality` / `sampled_at` / `quality` / `metrics` / `metadata` — a query contract |
| 2 | Physical stores | Keep typed-power + JSON-polled fork; never one sparse table |
| 3 | Cross-modal query layer | First-class `app/services/multimodal.py`; gates "final" schema |
| 4 | Transport | Direct HTTPS for plugs; pull for polled; MQTT internal-only; one normalized ingest |
| 5 | Adapter independence | Per-modality failure isolation; query layer works on partial input |
| 6 | Time sync | Measured, not assumed; coarse windows until G2 drift data |

## 9. Schema-review gate

`app/services/multimodal.py` (P3b) **must not be implemented** until a
reviewer has checked the Decision-1 envelope against a hypothetical
fourth modality and signed off here:

- [ ] Schema review complete — envelope survives a 4th modality. Reviewer: ___  Date: ___
