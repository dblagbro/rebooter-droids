# B16 — Power-usage monitoring + analytics: research, design, and roadmap

| Field | Value |
|---|---|
| Status | **Implemented** — Phases 1A–1D shipped v0.5.26–v0.5.32; P1.1–P1.3 data-path follow-through shipped v0.5.54–v0.5.59 (JSON power API, data-quality surfacing, interactive 24h chart). Loaded-power analytics validation still pending a firmware loaded-power capture. See `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6 (P1). |
| Authors | rebooter-droids design track |
| Created | 2026-05-10 PM |
| Companion docs | `docs/BACKLOG.md` B16, `docs/redesign-continuation-plan-v2.md` Tier F |
| Hardware | Sonoff S31 fleet — metering chip **CSE7766** (confirmed; real telemetry live on firmware `0.1.25-dev-central-safe`). HLW8032 was an early incorrect assumption. |

> Power-monitoring is a tempting feature precisely because it looks
> like it could be huge. This doc grounds the ambition: what's
> genuinely solid science, what's research-grade, what's
> aspirational-to-impossible at our hardware tier, and what the
> realistic v1 → v3 shipping plan looks like.

---

## 1. Executive summary

The CSE7766 on each Sonoff S31 measures **RMS voltage, RMS current,
active power, accumulated energy**, and (via zero-crossing)
**frequency**, with a UART packet every ~50 ms (~20 Hz realistic at
the API boundary). At 1 sample/second averaged + bursts on demand,
the per-device data is enough to build **four tiers of analytics**
that map cleanly onto realistic ML / signal-processing techniques
described in current literature:

| Tier | Ships | What it does | Confidence |
|---|---|---|---|
| **1. Cost + baseline** | v0.6.x | Per-device energy, $ cost, duty cycle, standby/phantom-load | **High** — pure accounting |
| **2. Behavioural anomaly** | v0.7.x | "Your fridge is running 40% more than last month", cycle-length outliers, sag-under-load trending | **High** — change-point detection on aggregates |
| **3. Cross-plug correlation** | v0.8.x | "When plug A's compressor starts, plug B sees a 4V drop on a different circuit → shared upstream impedance change" | **Medium** — research-grade, screening hint only |
| **4. Appliance-state pattern recognition** | v0.9.x | Compressor degradation / motor-load drift / appliance-class fingerprinting | **Medium-low** — ML-tier, needs months of baseline |

What we **will not ship** at this hardware tier: arc-fault detection,
bearing-fault MCSA, harmonic / THD analysis, sub-cycle transient
capture, definitive "you have loose wiring" diagnoses. Each of those
requires either kHz-MHz waveform access or panel-side correlation;
the CSE7766's exposed bandwidth and the smart-plug topology rule
them out.

The single most operator-meaningful feature is **Tier 3 cross-plug
correlation** because it turns the fleet into a distributed sensor
that's qualitatively richer than any single plug. That's what we're
optimising the whole architecture around.

---

## 2. Hardware reality check

### 2.1 CSE7766 capabilities (per public datasheet + Tasmota / ESPHome)

- **Voltage (RMS)**: ~120 V nominal NA, accuracy ~0.5 %.
- **Current (RMS)**: 0–15 A range, accuracy ~0.5 %, but low-current
  resolution (<100 mA) is noisy.
- **Active power (real W)**: ~0.2 % accuracy over a 1000:1 dynamic
  range; low-load (<3 W) response is windowed-averaged over ~8 s.
- **Frequency**: derived from zero-crossings. Resolution roughly
  ±0.1 Hz; enough to detect "on grid vs. on generator/UPS", not
  enough for utility-event-level diagnostics.
- **Accumulated kWh**: via pulse counter on the energy pin.
- **Apparent power, power factor**: not emitted directly by the
  CSE7766 over UART, but **computable** firmware-side as
  `S = V_rms × I_rms`, `PF = P / S`. Whether the current firmware
  computes and exposes these is one of our open questions to the
  firmware team.
- **UART cadence**: one packet every ~50 ms (~20 Hz). Internally
  the chip's ADC runs at ~0.89 MHz but we **cannot reach the raw
  waveform** through any documented register.

### 2.2 What the chip **cannot** do

- **Raw waveform capture.** No way to dump current/voltage samples
  faster than the averaged UART packets. Rules out harmonic
  analysis and motor-current-signature analysis (MCSA).
- **Harmonic / THD analysis.** Needs current waveform; requires
  ATM90E32 or ADE9000-class chip (~$3-5 more BOM).
- **Arc-fault detection (AFCI / AFDD).** Per IEC 62606, AFDDs
  monitor the 10 kHz – 1 MHz noise band. CSE7766's exposed
  bandwidth is sub-50 Hz. **Fundamentally impossible at this tier**
  — don't promise it.
- **Sub-cycle transient capture.** A 60 Hz cycle is 16.7 ms; a
  sub-cycle transient ends inside one CSE7766 packet window.

### 2.3 What this means for the protocol

The realistic per-device stream is **steady-state 1 Hz aggregates**
plus **on-demand 10-20 Hz bursts** for short windows. We design
both into the protocol from day one.

---

## 3. Data model

### 3.1 New tables

```
device_power_samples
├── id           bigint PK
├── device_id    varchar(40) FK → devices.id ON DELETE CASCADE
├── sampled_at   timestamptz NOT NULL    -- device-side clock, with skew correction
├── received_at  timestamptz NOT NULL    -- hub-side ingest time
├── v_v          numeric(6,2)            -- volts, e.g. 120.45
├── i_ma         integer                 -- milliamps, e.g. 1450
├── p_w          numeric(8,2)            -- watts, e.g. 175.30
├── s_va         numeric(8,2)            -- apparent VA (nullable)
├── pf           numeric(4,3)            -- power factor 0.000-1.000 (nullable)
├── hz           numeric(5,2)            -- frequency Hz (nullable)
├── energy_wh    bigint                  -- accumulated Wh since boot
└── source       varchar(20)             -- 'steady' | 'burst' | 'synthetic'
INDEX (device_id, sampled_at DESC)
INDEX (received_at)  -- for retention pruning
```

```
device_power_rollups
├── id              bigint PK
├── device_id       varchar(40) FK
├── bucket_start    timestamptz NOT NULL
├── granularity     varchar(10) NOT NULL  -- 'hour' | 'day' | 'week'
├── sample_count    integer
├── kwh             numeric(10,4)
├── avg_w           numeric(8,2)
├── peak_w          numeric(8,2)
├── min_w           numeric(8,2)
├── avg_v_v         numeric(6,2)
├── min_v_v         numeric(6,2)
├── max_v_v         numeric(6,2)
├── avg_pf          numeric(4,3)
├── duty_cycle_pct  numeric(5,2)  -- % of bucket where P > on_threshold_w
└── computed_at     timestamptz
UNIQUE (device_id, granularity, bucket_start)
```

```
device_burst_windows
├── id                  bigint PK
├── device_id           varchar(40) FK
├── started_at          timestamptz NOT NULL
├── ended_at            timestamptz                  -- NULL while in-flight
├── target_duration_s   integer NOT NULL
├── target_rate_hz      integer NOT NULL
├── source              varchar(20)                  -- 'scheduled' | 'on_demand' | 'anomaly_triggered'
├── status              varchar(20)                  -- 'requested' | 'collecting' | 'uploaded' | 'failed'
├── sample_count        integer
└── reason              text
INDEX (device_id, started_at DESC)
```

```
runtime_settings (existing, +new keys)
├── power.rate_per_kwh           — operator currency rate ($ / kWh)
├── power.currency_symbol        — '$', '€', '£'
├── power.sample_retention_days  — default 30
├── power.burst_window_default_s — default 86400  (24 h)
├── power.burst_cadence_days     — default 7      (every 7 days)
```

### 3.2 Retention strategy

- **Raw samples** (`device_power_samples`) kept for
  `power.sample_retention_days` (default 30). Nightly soft-prune
  job moves older rows into an `archive_*` table or deletes
  outright depending on `power.archive_raw` setting (deferred).
- **Rollups** (`device_power_rollups`) kept **forever**. A device
  running 24/7 for a decade generates ~3650 daily rollup rows +
  ~87,600 hourly rollup rows — cheap. (8 bytes/numeric * ~15 cols
  * 100k rows ≈ 12 MB / device / decade.)
- **Burst windows** (`device_burst_windows`) kept forever; raw
  samples linked to them are retention-locked separately to
  survive the 30-day cap (operator may want to look at the
  burst-window data months later).

### 3.3 Ingestion API

```
POST /api/v1/device/power-samples
Headers: Authorization: Bearer <device_token>
Body:
  {
    "device_id": "dev_…",
    "samples": [
      {
        "sampled_at": "2026-05-11T03:00:00.123Z",
        "v_v": 120.4,
        "i_ma": 1450,
        "p_w": 175.3,
        "s_va": 178.2,             # optional
        "pf": 0.945,               # optional
        "hz": 60.01,               # optional
        "energy_wh": 1234,         # accumulated since device boot
        "source": "steady"         # or "burst"
      },
      …
    ]
  }

Response 200:
  { "ok": true, "data": { "ingested": <n>, "next_steady_rate_hz": 1 } }
```

- Up to **3600 samples per batch** (1 hour at 1 Hz, or ~3 minutes
  at 20 Hz burst rate). Batch size is operator-tunable via
  `power.max_batch_samples` runtime setting.
- Auth: existing device-bearer token; same flow as `/device/events`.
- Idempotency: a `(device_id, sampled_at)` UNIQUE constraint on the
  raw table makes re-sends of a buffered batch safe (the device
  can retry on partial network failures).

### 3.4 Burst-window control plane

The operator can request "intensive monitoring for 24 h starting
now" or "scheduled rotating burst windows every 7 days at the next
Sunday 03:00 UTC". This is a **command-queue** thing, reusing the
existing `commands` table.

```
POST /api/v1/admin/devices/{device_id}/commands
Body:
  {
    "type": "start_burst_window",
    "payload": {
      "duration_s": 86400,
      "rate_hz": 10,
      "reason": "weekly_rotation"
    },
    "ttl_seconds": 120
  }
```

- The device picks up the command on next poll, locally buffers at
  `rate_hz` for `duration_s`, and uploads in chunks via
  `/device/power-samples` as it goes (not at the end — uploads
  should drain steadily so a power-cycle mid-window only loses a
  partial chunk).
- A nightly scheduler job ("burst-rotation planner") looks at the
  fleet, picks the next device due for its weekly burst, and
  issues the command. The schedule rotates day-of-week so over a
  4-8 week cycle every device gets coverage across every weekday.

---

## 4. Analytics architecture by tier

### 4.1 Tier 1 — Cost + baseline (v0.6.x, ~10h)

**What ships**:
- Live last-sample card on `/app/devices/<id>` Power tab.
- "Today / This week / This month / Custom range" kWh + cost
  numbers using `power.rate_per_kwh`.
- Per-device duty cycle: "your device was drawing > 5 W for 47%
  of the last 24 h".
- Standby/phantom-load: "this device drew ~2.3 W continuously
  even when nominally 'off'".
- Fleet-wide `/app/power` page: sortable table of devices by kWh,
  top-N "biggest hogs" of the last 7 days.

**Why high-confidence**: pure accounting. The rollup table makes
range queries fast. No ML, no inference. Worst-case failure mode
is "the cost number is off by 2 %" which is bounded by the
chip's accuracy.

**Implementation**: hourly rollup job using existing APScheduler
infrastructure. ~10 h of hub-side work.

### 4.2 Tier 2 — Behavioural anomaly (v0.7.x, ~12h)

**What ships**:
- Trend channel on each device's Power tab: "your fridge's daily
  kWh has trended up 23 % over the last 30 days".
- Cycle-length distribution: per-device histogram of "compressor
  on" event durations; alerts when a new cycle falls > 3σ from
  the established distribution.
- Voltage-sag-under-load trending: at every transition from
  P<10 W to P>100 W, record `(v_steady_before, v_at_peak_p)`. The
  delta is the upstream-impedance proxy. Trend it over weeks.
- Standby-load drift alerts.

**Method**: per-device seasonal decomposition (STL or simpler
exponential-smoothing) on hourly rollups; the trend channel is
the degradation signal. Change-point detection on the trend
(`ruptures` library) with operator-tunable sensitivity. No deep
learning — overkill at this fleet size.

**Why high-confidence**: STL + change-point is mature. Sag-under-
load trending is novel in the consumer-plug context but the
underlying physics (V = V₀ - I·Z_upstream) is solid. The risk is
false positives, mitigated by long windows + operator-friendly
"hint, not diagnosis" framing.

**Implementation**: ~12 h. New `app/services/power_analytics.py`.
A nightly aggregation job + an on-the-fly recompute when an
operator opens a device's Power tab. Stores results back as
rollup metadata + an `attention_items` row when an anomaly fires.

### 4.3 Tier 3 — Cross-plug correlation (v0.8.x, ~15h)

**The MIT-research-tier piece, scoped honestly.**

**The insight**: when plug A on circuit α starts its compressor
and draws 1500 W of inrush, every plug in the house sees a
voltage sag. The amount of sag depends on the impedance path:

- Plugs on the **same branch** as A see the biggest drop (high
  shared impedance).
- Plugs on a **different branch but same panel** see a smaller
  drop (only panel + service-entrance impedance is shared).
- Plugs on a **different building / transformer** see almost
  nothing.

If we capture this sag pattern across the fleet at every major
inrush event and **track how it drifts over time**, we can detect:

| Drift pattern | Plausible cause |
|---|---|
| All plugs see same fixed drop, slowly growing | Service-entrance corrosion, neutral degradation, transformer issue |
| One branch's plugs see growing drops; other branches stable | Loose breaker, degraded branch wiring |
| One plug's drops grow but its branch-siblings don't | Outlet-level loose connection at that one plug |

**What ships**:
- Cross-plug inrush correlation table: at every detected inrush
  on any plug, sample voltage at all plugs in the same site +
  store the joint observation.
- Pattern-recognition engine: classify the sag pattern as
  service-entrance / branch / outlet using the three cases above.
- Drift trends per classification: "outlet-level drop at
  `Erica's Subwoofer` has grown from 1.2 V to 3.7 V over 8 weeks.
  Have an electrician check the receptacle and breaker."

**Why medium-confidence**: the physics is solid; the
correlation-extraction is well-studied (cross-correlation in
time-series); the false-positive control is the hard part. Need
a minimum baseline of 6-8 weeks before any alert fires. Always
framed as "have an electrician verify" — never "you have loose
wiring".

**What we explicitly do not claim**: arc-fault detection (still
impossible at this hardware tier), or any inference about wires
behind walls that don't share an impedance path with our plugs.

**Implementation**: ~15 h. New event-correlation worker that
runs on every batch of new samples. Storage: a
`device_inrush_events` table + a `cross_plug_correlation` table.
Heavy use of the `numpy` / `scipy` stack — adds modest container-
size overhead but no new external services.

### 4.4 Tier 4 — Appliance-state pattern recognition (v0.9.x+, ~20h)

**What ships**:
- Per-device appliance-class inference (fridge / motor /
  heating-element / electronics / unknown) based on startup
  PF dip, steady-state P, duty cycle.
- Within-class anomaly detection: "this device's startup PF dip
  has shifted from 0.22 to 0.31 over 6 months — typical
  signature of motor-winding insulation degradation". Always
  paired with class-appropriate caveats.
- Appliance-fingerprint library: collect anonymised
  `(class, signature)` tuples across the fleet for operator
  inspection ("your appliance most closely matches: residential
  fridge compressor, 14 cu ft class").

**Why medium-low confidence**: needs 3-6 months of baseline per
device before alerts mean anything. Appliance-class inference is
well-studied (this is the easy half of NILM) but the
"degradation signature" piece is genuinely research-grade for
plug-level data without raw waveforms. Likely false-positive
rate is high enough that this tier ships as
**"insights, not alerts"** — surfaced on a dedicated page that
the operator opens deliberately, never as a notification.

**Implementation**: ~20 h, but probably a 3-4 ship arc spread
across v0.9.x and v0.10.x. Uses the existing rollup + correlation
data — no new tables. May want to add `nilmtk` as a dev-dep for
the classification experiments without making it a runtime
dependency.

---

## 5. The burst-monitoring cadence

The operator proposed: 24 hours of intensive sampling every 4-8
days, rotating day-of-week so over a couple of months every
weekday gets coverage at each device.

**Why this is the right shape**:
- 1 Hz steady-state samples *forever* would be 86,400 rows/day
  × 30 days × 50 devices = 130 M rows. Sustainable but heavy.
- 1 Hz steady-state always + 10-20 Hz bursts in a 24h window
  every 7 days = much smaller storage footprint with the
  finest-grained data exactly when we're doing analytics.
- Day-of-week rotation catches workday/weekend usage
  differences without paying for 7×24-hour-per-week capture.

**Schedule policy** (operator-tunable):
- Default rotation: every 7 days, starting on a different
  day-of-week each cycle (so cycle 1 = Mon, cycle 2 = Tue, …).
  After 7 cycles (49 days) every day-of-week has been covered.
- Burst rate during a window: default 10 Hz (~864,000 samples
  per 24h per device — heavy but bounded; ~17 MB raw per
  burst-window per device pre-compression).
- Off-window cadence: 1 Hz averaged + 1 sample per 60 s
  uploaded as the steady stream.
- Override paths: operator can request an on-demand burst
  ("monitor this device intensively for 1h starting now") via a
  button on the device detail page.

**Implementation**: the burst-window control-plane (§3.4) is
about ~3 h of hub-side work. Lives in Tier 1 because Tiers 2-4
all want the burst data eventually.

---

## 6. Privacy + ethics

This data is famously sensitive:

> Per-plug power data at 1 Hz lets a malicious recipient infer
> meal times, sleep/wake cycles, shower events, "alone vs.
> company at home", TV/console usage patterns, and bathroom
> visits, with >80% accuracy per multiple published methods
> (McLaughlin et al. CCS '11 onwards).

Per-plug data is **strictly worse for privacy than aggregate
meter data** because the appliance is pre-labelled — you don't
even need NILM to know the user just opened the fridge at 03:14.

**Design mitigations baked in from day one**:

1. **Local-first by default.** Raw 1-Hz samples and burst-window
   raw data stay on the hub. Aggregates (hourly + daily rollups)
   are the only things shipped over any future cross-hub-sync
   channel.
2. **Aggregation windows**. Default UI surface shows 5-min
   averages; 1-sec resolution available behind a "show fine
   detail" toggle that audit-logs the view.
3. **Per-plug ACL.** Aligns with B10's `Site + Group + Device`
   scope. Bathroom/bedroom plugs may warrant tighter retention
   rules than kitchen plugs; this is set per-device.
4. **No "occupancy" features exposed to the UI even if
   technically derivable.** Strict line: aggregate energy, cost,
   duty-cycle, anomaly hints — yes. Inferred-occupancy heatmaps
   — no.
5. **No external cloud transmission by default.** Optional
   anonymised opt-in for "share my anonymised baseline data to
   help train the cross-fleet appliance-class model" — and even
   that is deferred until at least Tier 4.
6. **Data deletion is hard-delete.** When a device is unenrolled
   or an operator clicks "purge this device's data", we delete
   all rows in `device_power_samples`, `device_power_rollups`,
   `device_burst_windows`, `device_inrush_events`. Not soft-
   delete. Backups follow a 30-day rotation so even the backup
   chain rolls off in a bounded time.

**Documented in `docs/PRIVACY.md` once Tier 1 ships** so
operators have a clear statement of what is and isn't inferable.

---

## 7. Shipping plan

Eight ships across v0.6.x → v1.0.x. Listed in dependency order;
each lands behind a feature flag `power_monitoring.enabled`
(off by default) until Tier 1 is operator-validated.

| Ship | Tier | Scope |
|---|---|---|
| **v0.6.0** | T1 | Schema migration: `device_power_samples`, `device_power_rollups`, `device_burst_windows`. `POST /api/v1/device/power-samples` endpoint. Synthetic-sample injector for testing without firmware. |
| **v0.6.1** | T1 | Power tab on device detail. Live last-sample card. Today / week / month kWh + cost. Rate-per-kWh runtime setting. |
| **v0.6.2** | T1 | Fleet `/app/power` page. Top-N hogs. Duty-cycle + standby/phantom-load display. |
| **v0.6.3** | T1 | Burst-window control plane: `start_burst_window` command type + scheduler-driven rotation + on-demand button. |
| **v0.7.0** | T2 | Per-device seasonal-decomposition trend channel + change-point detection. Trend alerts as attention-items. |
| **v0.7.1** | T2 | Sag-under-load trending. Cycle-length distribution + anomaly alerts. Standby-load drift. |
| **v0.8.0** | T3 | Cross-plug inrush correlation worker. Classification (service-entrance / branch / outlet). Per-classification drift alerts framed as electrician-screening hints. |
| **v0.9.x+** | T4 | Appliance-class inference + within-class degradation signatures. Surfaced as "insights" page, not alerts. NILM-adjacent ML; ~3-4 ships. |

**Feature-flag policy**: each tier's flag defaults off; operator
enables when comfortable. Tier 1 is the "trust this enough to
make it default-on" milestone — probably v1.0.0 of rebooter-droids.

**Firmware dependencies**:
- Tier 1 ingestion can ship + be validated against synthetic
  samples before firmware emits anything. Hub work is fully
  unblocked.
- Tiers 2-4 need real device samples to be meaningful but the
  *code* can ship against synthetic data first.
- Burst-window mode (v0.6.3) needs the firmware to support the
  `start_burst_window` command. Until then, hub can run
  rotation against a synthetic-only fleet for QA.

---

## 8. Open questions for firmware team (asked 2026-05-10 PM)

Sent 2026-05-10 PM in a cross-team note. Summarised here for
implementer's reference:

1. **Confirm chip**: CSE7766, HLW8032, BL0942, or something else
   on the live fleet?
2. **Sample rate ceiling**: what can the firmware emit per second
   without compromising heartbeat / watchdog responsiveness?
   Working assumption: 1 Hz steady, 10-20 Hz burst.
3. **Local buffering**: can the firmware buffer ~24 h of samples
   for the burst-rotation pattern? At what rate?
4. **PF + frequency**: are these computed firmware-side or do we
   need to derive them on the hub from `V`/`I`/`P`?
5. **Calibration**: per-unit cal needed or factory-default
   adequate?
6. **Raw waveform**: any backdoor to dump 1 s of waveform
   samples on demand? (Probably not, but worth asking.)
7. **Version bump**: when relay_off bug fix lands, please ship
   as ≥ `0.1.6-dev-central` so hub's upgrade button picks it up.

## 8b. DECIDED 2026-05-10 PM — firmware-team answers

Firmware-team replied 2026-05-10 PM (item 7 also closed — 0.1.6
shipped, hub registered it after v0.4.34's `os.sync()` fix).
Constraints land below; they tighten parts of §3 and §4.

### Chip identity (Q1) — not yet code-truthful

Firmware does **not currently probe or read any metering IC at
all**. CSE7766 is "the most likely chip" per Sonoff S31
hardware-family expectations, but treat as a hardware expectation,
not a firmware-proven fact, until either:
- the firmware adds an actual meter driver and confirms it
  enumerates, OR
- a unit is physically inspected to confirm the chip silkscreen
  / package marking.

**Hub-side implication**: design the ingestion contract
**chip-agnostic**. Every field except `v_v`, `i_ma`, `p_w`,
`energy_wh` is nullable in `device_power_samples` so we can ship
even if the eventual chip exposes fewer values than the CSE7766
would. No chip-name baked into table names or column names.

### Current firmware emits nothing (Q2)

No code path today reads the meter chip. **Tier-1 ingestion will
ship against synthetic samples first** (already the plan) and
have nothing real to ingest until firmware adds the driver.
That's fine for the v0.6.0/v0.6.1 ships — we want the storage +
UI proven on synthetic data before any real device emits.

### Cadence (Q3) — 1 Hz steady-state, batch upload

Firmware-team confirms: 1 Hz steady is reasonable; do NOT do
one HTTP POST per second; batch on the device side and upload
periodically. Burst rates above 1 Hz are "possible later, but we
should not promise that until we measure actual CPU, serial, and
filesystem behavior on the live ESP8266 build."

**Hub-side decision (overrides §5's 10 Hz burst-window default)**:
- **steady-state default**: 1 Hz samples, batched at 60-sample
  granularity (1 minute per batch) by default. Operator-tunable
  via runtime setting `power.steady_batch_seconds`.
- **burst-window default**: drop from 10 Hz → **1 Hz with a
  shorter batch interval** (e.g. 10-second batches for near-
  real-time visibility), until firmware proves higher rate is
  safe on the live build. The `device_burst_windows.target_rate_hz`
  column stays as designed; the *default* is now 1.
- **Plan revision**: the "v0.6.3 burst-window control plane" ship
  is still valid, but its v1 sets `rate_hz=1` and we re-evaluate
  raising it after the firmware team measures load.

### 24h buffer (Q4) — cautious; 10 Hz unrealistic

Firmware-team: "On ESP8266, 24 hours of raw 1 Hz storage may be
possible only with a compact binary format and careful space
budgeting; 24 hours of 10 Hz raw buffering is not a realistic
first target on this footprint."

**Hub-side decision (overrides §5's "24 h burst every 7 days"
default)**:
- The **operator-facing default cadence stays "intensive
  monitoring window every 7 days"** but the *intensity* drops:
  during a burst window the device samples at the same 1 Hz it
  does at steady-state, with a tighter batch upload interval
  (10 s vs 60 s) so the hub gets near-real-time data during the
  window. No new compression / packing on the device side
  required.
- The **"raw 10 Hz for 24 h"** mode is **deferred** as a v2
  feature that's gated on:
  1. firmware-team prototyping a compact binary buffer format
     for ESP8266 flash/RAM
  2. measurement of actual sustainable rate on the live build
- Hub-side ingestion API contract stays as-is (accepts batches
  with any `source` value); the change is purely default
  cadence + a more conservative `target_rate_hz` ceiling on the
  control-plane endpoint.

### Power factor + frequency (Q5) — not computed today

Current firmware does not compute / expose PF or frequency.
Plan to add when the metering path lands; whether they're
exposed depends on whether the chosen chip/driver gives them
cleanly enough.

**Hub-side implication**: `pf` and `hz` columns in
`device_power_samples` stay **nullable**. Analytics tiers that
depend on them (Tier 4 startup-PF-dip drift detection,
generator/UPS frequency-drift detection) gracefully degrade to
"insufficient data" when the columns are null. This was already
the design.

### Calibration (Q6) — not implemented

No calibration logic in firmware today. Firmware-team
expectation: factory calibration is probably adequate for
trend / relative analytics; sub-2% absolute accuracy claims need
either a firmware calibration mode or per-unit validation
against a known reference.

**Hub-side decision**:
- **Tier 2 trend / drift detection** does not need calibration —
  it's all relative-to-prior-baseline. Ship it on factory cal.
- **Tier 1 dollar-cost reporting** is sensitive to absolute
  accuracy. Frame the $ number with "±2% typical" disclosure
  text in the UI and add an operator-settable per-device
  `power.cal_offset_pct` runtime setting (default 0) so an
  operator with a clamp meter can dial in a unit if they care.
- **No "calibrate now" workflow** in v1. Add to the v2 backlog.

### Raw waveform snapshot (Q7) — assume no

Firmware-team: "assume no practical raw waveform dump path
through the current planned device stack." Matches the research
agent's "hardware impossible at this tier" answer for CSE7766's
public UART path.

**Hub-side implication**: harmonic / THD analysis, MCSA
bearing-fault detection, arc-fault detection stay in §9
("deliberately do not promise"). Doc unchanged on this point.

### Firmware-team-suggested sequencing (added 2026-05-10 PM)

Firmware-team recommended order:
1. finish rolling 0.1.6 onto remaining older 0.1.3/0.1.5 devices
2. resolve hub firmware-scan / catalogue mismatch
   (✅ resolved 2026-05-10 PM by hub v0.4.34's `os.sync()` fix —
   scan now picks up 0.1.6 on first invocation)
3. then start metering / power-telemetry implementation spike

Aligns with our existing ship plan in §7: Tier 1 hub-side
ingestion (v0.6.0) can land while firmware-team works the
metering driver spike in parallel; the hub-side surface ships
against synthetic samples first regardless.

### What this section did NOT change

- §1 four-tier executive summary — same.
- §3.1 + §3.2 table shapes — same (all the new constraints are
  default-value or nullability tightenings, not schema changes).
- §3.3 ingestion API contract — same field set; defaults adjust.
- §4 analytics-tier scoping — same. Tier 4's PF-dip drift is
  marked "gracefully degrades when pf is null" but otherwise
  unchanged.
- §6 privacy posture — same.
- §7 ship plan — same; v0.6.x dates not yet committed and we
  keep the synthetic-samples-first approach so firmware-team can
  work the metering spike on their own track.
- §9 "will not promise" — same; raw waveform / arc-fault /
  harmonic stay firmly on the do-not-promise list.

---

## 9. What this doc deliberately does NOT promise

- **Arc-fault detection.** Hardware impossible at this tier.
- **Motor-bearing fault prediction via MCSA.** Needs raw current
  waveform; chip doesn't expose it.
- **Harmonic / THD analysis.** Same reason.
- **Sub-cycle transient capture.** Same.
- **Definitive "you have loose wiring" alerts.** Tier 3
  classification is framed as electrician-screening hints, never
  as a diagnosis. False positives on a "have an electrician
  inspect" alert are costly (operator pays for an unnecessary
  call-out) but recoverable; false-positives on "you have loose
  wiring" would erode trust in the whole system.
- **External cloud transmission of raw data.** Aggregates only,
  opt-in only.
- **Real-time streaming via WebSocket.** Polling at 1-10 s is
  enough for every UX we've scoped. Defer until there's a
  concrete UX need.
- **On-device ML.** ESP8266 has 80 KB RAM. Threshold rules and
  buffered upload only; analytics happens on the hub.

---

## 10. Decisions captured here

1. **Hardware truth**: CSE7766 on Sonoff S31 (pending firmware-
   team confirmation). HLW8032 was an incorrect assumption in
   v1 of B16.
2. **Architecture**: 1 Hz steady-state + 24-h burst windows
   every 7 days, rotating day-of-week.
3. **Tiering**: four analytics tiers from pure accounting
   (Tier 1) to ML-adjacent appliance fingerprinting (Tier 4).
4. **Cross-plug correlation is the MIT-tier feature**, not
   single-plug deep inference. Frame: turn the fleet into a
   distributed sensor that's qualitatively richer than any
   individual plug.
5. **Privacy posture**: local-first, aggregation-by-default, no
   occupancy features exposed, hard-delete on unenrolment.
6. **All alerts framed as screening hints**, never as
   diagnoses. Electrician-call alerts require ≥ 6-8 weeks of
   baseline data and have multi-plug confirmation.
7. **Feature flag** `power_monitoring.enabled` defaults off
   through v0.6.x → v0.9.x; flips on at v1.0.0 after operator
   validation.

---

## Appendix A — Research sources

Cited in the 2026-05-10 PM research pass. State-of-the-art in
NILM, smart-plug analytics, IEEE 1159 power-quality, AFCI/AFDD
standards, and privacy attacks on energy data, current as of
2024-2026 literature:

- HLW8032 / CSE7766 datasheets (vendor public docs)
- Tasmota + ESPHome integration docs
- NILMTK ([nilmtk.github.io](https://nilmtk.github.io/)) — canonical NILM Python toolkit
- Zhong et al., seq2point-nilm (foundational deep NILM)
- NILMFormer (arXiv 2506.05880, 2025)
- ELECTRIcity transformer NILM (PMC 2022)
- DiffNILM diffusion-model NILM (PMC 2023)
- IEEE 1159-2009/2019 Power Quality Recommended Practice
- IEC 62606 AFDD standard (arc-fault detection)
- McLaughlin et al., "Protecting Consumer Privacy from Electric
  Load Monitoring" (CCS 2011)
- "Thwarting Nonintrusive Occupancy Detection Attacks"
  (Hindawi 2017)
- "Combined Heat and Privacy" (UMass LASS, 2014)
- MIT EMSG, "Nonintrusive Motor Current Signature Analysis"
- Federated Learning for Energy Anomaly Detection
  (arXiv 2502.05041, 2025)

Full citation list captured in the 2026-05-10 PM research log.
