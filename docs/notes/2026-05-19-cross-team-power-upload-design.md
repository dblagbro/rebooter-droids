# Cross-Team Design Proposal — Constrained-Device Power-Upload Transport

**Date:** 2026-05-19
**From:** Rebooter-Droids hub team (operator-released)
**To:** Firmware team + future engineers picking up this thread
**Status:** SHAREABLE — released under operator authority 2026-05-19, lifting the
prior HOLD on the internal hub-team analysis.

> This document is the hub team's design proposal in response to the firmware
> team's 2026-05-17 low-heap power-upload memo and the 2026-05-19 cold-start
> dossier. It is now the agreed direction (per operator decision D-001 / D-011)
> unless firmware-team review surfaces a hard technical objection.

---

## 1. Context — what we are solving

Per the firmware team's findings:

- **Stable on low-heap ESP8266 wall units (`.225`, `.69`):**
  - `central=true, power=false`
  - `central=false, power=true`
- **Unstable on the same units:**
  - `central=true, power=true`
- The progression `0.1.38 → 0.1.39 → 0.1.40-dev-central-safe` trimmed
  response-body allocation, tightened upload cadence, and added startup delay.
  `.69` still hit `reset_reason="Exception"` during `central+power`.
- Firmware-team conclusion: the constrained ESP8266 path **cannot tolerate a
  separate HTTPS round-trip** to `/api/v1/device/power-samples` under current
  heap limits — not that the JSON payload itself is too big.

Firmware team proposed three directions:
- **(A)** Heartbeat-carried compact summary
- **(B)** Lighter dedicated ingest endpoint
- **(C)** Class-based transport policy

## 2. Hub-team scoring (revisited and confirmed)

| | (A) Heartbeat piggyback | (B) Lighter dedicated | (C) Class-based router |
|---|---|---|---|
| Eliminates a TLS handshake? | **yes** — folds into existing heartbeat connection | no — still a second HTTPS round-trip | depends on which it picks |
| Per-sample fidelity | drops to 1 / heartbeat interval (~60 s) | preserved (firmware-cadence) | depends |
| Hub-side complexity | small (~30 LOC + `source="heartbeat"` enum value) | medium (new endpoint + parallel maintenance) | larger (taxonomy + per-class router) |
| Schema migration | none (reuses `DevicePowerSample` columns) | none–small | none |
| Adds heartbeat-contract coupling? | yes — additive, optional, nullable | no | depends |
| Generalises to future device classes | partial | no | yes (that's its purpose) |
| Likely to solve `.225` / `.69` exception reboots | **yes**, if HTTPS handshake is the killer | only if response parsing is the killer | C-with-A: yes ; C-with-B: same as B |

## 3. Decision

**Adopt Option A. Ship it first. Leave B and C as fallback structures, not v1 builds.**

### Why

1. **The firmware data already names the TLS round-trip as the suspect**, not
   JSON parsing. `0.1.40` cut response-body allocation and per-upload heap
   chatter — and `.69` still crashed. (B) doesn't change that fact; it
   only trims further. (A) avoids the second round-trip entirely by reusing
   the heartbeat connection.
2. **The fidelity loss is acceptable for wall-installed ESP8266 units.**
   1 sample per minute is enough for cost/kWh + B16 power-page dashboards on
   these constrained units. The high-resolution path (per-100 ms / per-1 s)
   remains available on roomier hardware via the existing dedicated endpoint.
3. **(C) is naturally a wrapper, not an implementation.** We can lift Option A
   into a per-class policy later by adding one flag check: "if device class is
   `esp8266`, use heartbeat path; otherwise dedicated endpoint."

### Why not (B) alone

(B) is the right answer **only** if response-body parsing is the heap killer.
The three-version firmware progression strongly suggests it isn't.
Connection-establishment overhead is what's left to trim, and (B) doesn't
trim that — it still does TLS + TCP setup for every power upload. We'd ship
(B), still see Exceptions on `.225` / `.69`, and backtrack to (A).

### Why not (C) first

Building a class-router before any of its routes work is over-engineering.
"We have two unstable devices, every other device is fine" doesn't justify a
class taxonomy. Start with (A); generalize to (C) only if and when a second
device class needs a different policy.

## 4. Contract — `power_compact` in the heartbeat

The heartbeat JSON body gains one optional field:

```json
{
  "...": "(existing heartbeat fields unchanged)",
  "power_compact": {
    "p_w":            123.4,
    "v_v":            122.7,
    "i_ma":           1014,
    "i_ma_estimated": false,
    "i_ma_estimate":  null,
    "source_flags":   0,
    "sampled_uptime_seconds": 87421,
    "valid_frame_count":   942,
    "invalid_frame_count": 507
  }
}
```

### Required vs. optional

- **Required if `power_compact` is present:** `p_w` (the latest watts reading,
  float).
- **Optional, in heap-affordability order — keep what fits, drop the rest:**
  `v_v`, `i_ma`, `i_ma_estimated`, `i_ma_estimate`, `source_flags`,
  `sampled_uptime_seconds`, `valid_frame_count`, `invalid_frame_count`.

### Why the frame counts are folded in

The firmware team's open ask in the BACKLOG is that
`power_valid_frame_count` / `power_invalid_frame_count` are on
`/api/status` but not in the heartbeat — so the hub can't chart UART health
over time. Including them under `power_compact` closes that ask in the same
change.

### Hub-side: no new schema migration

`DevicePowerSample` already has columns for every field above. The only
schema change is adding `"heartbeat"` to the existing `source` enum
allow-list in `app/services/events.py`.

## 5. Hub-side implementation

The hub-side change is additive, opt-in, and harmless for roomy devices that
keep using the dedicated endpoint.

### Code change (~30 LOC + 3 tests)

```python
# In app/services/heartbeats.py, after the existing reported_config stash:

power_compact = body.get("power_compact")
if isinstance(power_compact, dict) and power_compact.get("p_w") is not None:
    from app.services.events import ingest_compact_power_sample
    ingest_compact_power_sample(session, device.id, power_compact, now)
```

```python
# In app/services/events.py:

def ingest_compact_power_sample(session, device_id, payload, sampled_at):
    """Write one DevicePowerSample row with source='heartbeat'.

    Maps {p_w, v_v, i_ma, ...} into the existing DevicePowerSample columns.
    Required: p_w. All other fields nullable.
    """
    # ... explicit field mapping, no kwarg pass-through
```

### Heartbeat response — no bloat

The heartbeat response stays the current ~84-byte shape:

```json
{"ok": true, "data": {"next_poll_after_seconds": N, "next_heartbeat_after_seconds": N}}
```

No separate JSON payload for the device to parse. The ack is implicit in the
200 status.

### Opt-in gate

A per-device runtime setting `power.heartbeat_path_enabled` (default
**false**) gates the hub-side read.

- Firmware emits `power_compact` only when the gate is on.
- Hub ignores `power_compact` from devices where the gate is off (defense in
  depth: even if firmware ships it before the gate flips, the hub won't
  double-ingest).
- Rollout: flip the gate per device once `.48` soak has passed.

### Observability

- `source="heartbeat"` makes it trivial to distinguish heartbeat-path samples
  from dedicated-endpoint samples in `/app/power` charts.
- A new column on the fleet `/app/power` "biggest hogs" table:
  `power source: heartbeat | dedicated | synthetic`.

### Tests (in `tests/unit/`)

1. **Unit** — `ingest_compact_power_sample` writes one row with
   `source="heartbeat"`; `p_w` lands; nullable fields handle absence.
2. **Unit** — `record_heartbeat` calls `ingest_compact_power_sample` exactly
   once per heartbeat when `power_compact` is present, never when absent.
3. **Integration** — POST a heartbeat with `power_compact` to a gated app;
   assert one `DevicePowerSample` row appears with `source="heartbeat"` and
   the gate is honored.

## 6. Firmware-side implementation guidance

The firmware change lands in `status_payload` (the shared serializer
described in the dossier's "Implementation note" section). It is fully
additive: heartbeat callers see one more optional object, nothing else
changes.

### Suggested approach

1. In `src/status_payload.cpp`, after the existing `reported_config`
   serialization, append `power_compact` only if **all** of:
   - `power.enabled == true`
   - `power.heartbeat_path_enabled == true` (new config field; D-011)
   - The latest CSE7766 sample is fresh (`power_last_sample_age_seconds <
     heartbeat_interval`)
2. Use the minimum-viable shape first: `{"p_w": <float>}`. If steady-state
   heap allows, append `v_v`, `i_ma`, frame counts, etc.
3. **Heap budget guideline:** any new heap allocation in this path under 50 B
   total (the JSON serializer should write directly to the heartbeat buffer
   without a temporary `JsonDocument`).

### What the firmware team can decide locally

- Exact JSON serialization technique (streaming vs. arduinojson vs. raw).
- Which optional fields fit within heap budget on `.225` / `.69`. Minimum is
  `{"p_w": …}`.
- Whether to also fold `power.heartbeat_path_enabled=true` into the
  `reported_config` block so the hub sees the device's actual mode.

### What the hub team commits to

- Hub-side read path lands first (Sprint 1, additive, harmless if firmware
  doesn't emit `power_compact`).
- Per-device gate flag (Sprint 1).
- 24-hour bench soak observation support — the `/app/power` page will show
  which path each sample arrived via.
- Rollback path — if firmware change causes regressions, hub side stays
  active and harmless; firmware-side gate can be flipped off via desired
  config push.

## 7. Rollout plan

1. **Sprint 1 (hub-side):** ship `ingest_compact_power_sample` + heartbeat
   read + per-device gate. Default OFF for every device. No firmware change
   needed.
2. **Sprint 1 verification:** confirm no behavior change on `.48` and wall
   devices (the gate is off; the new code path is unreachable).
3. **Sprint 2 (firmware-side):** ship `power_compact` emission in
   `status_payload`. Build, flash to `.48`.
4. **`.48` bench soak:** 24 hours with `central=true + power=true +
   heartbeat_path_enabled=true`. Pass criteria:
   - no `reset_reason=Exception` reboots
   - `power_compact` data shows up in hub `/app/power` with
     `source="heartbeat"`
   - steady-state free heap ≥ 14 KB
5. **Wall rollout:** OTA + gate-flip per device in order
   `.67` → `.30` → `.69` → `.225`. 6-hour soak per device before next.

## 8. Fallback paths if (A) is insufficient

These remain on file but are **not** v1 builds.

### Fallback to (B) — lighter dedicated endpoint

If `.225` / `.69` still Exception-reboot after Option A is rolled and gated
on:

- Add `POST /api/v1/device/power-samples-light` with `204 No Content` ack.
- Hub-side: ~40 LOC, mostly a thin alias to existing `ingest_power_samples`
  with the response-body suppressed.
- Firmware switches the dedicated endpoint to the `-light` variant on
  constrained units.

### Fallback to (C) — class-based router

Becomes one config field:

```python
# device.capabilities or new column device.class_tier
if device.class_tier == "esp8266":
    expect_path = "heartbeat"
else:
    expect_path = "dedicated"
```

Both transports stay available; the hub emits a soft alert if a device sends
on the wrong path for its class.

## 9. Open prep-questions for firmware team

These don't gate Sprint 1 hub work; they refine the firmware-side change in
Sprint 2.

1. **Heap-affordable subset.** Which of the optional `power_compact`
   sub-fields fit within the steady-state heap budget on `.225` / `.69`?
   Minimum viable shape is `{"p_w": …}`; everything else is gravy.
2. **Sample-freshness window.** What is a sensible "stale sample, drop it"
   threshold? Default proposal: `min(heartbeat_interval, 90 s)`.
3. **`reported_config` integration.** Should `power.heartbeat_path_enabled`
   appear in the `reported_config` block too? Default: yes (we want the
   hub-side drift detector to see the live device-side gate state).

## 10. Why the HOLD lifted

The HOLD on this document existed because the hub team did not want to
engage the firmware team mid-thrash without operator alignment. Three things
make engagement appropriate now:

1. The firmware team's 2026-05-19 cold-start dossier formally requests
   cross-team design help on exactly this question.
2. The dossier's retroactive communication audit (lines 27–185) is an
   explicit invitation to re-establish the shared channel.
3. Operator authority was assumed on 2026-05-19, with explicit instruction
   to convert blockers into decisions rather than waiting.

The HOLD is lifted; this document is now the shared baseline.

---

*Reply via the rebooter-droids repo — commits to `docs/notes/` are the
canonical communication channel going forward.*
