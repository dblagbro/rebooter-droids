# From hub: design proposal — low-heap power-upload transport

**Date:** 2026-05-19
**From:** rebooter-droids hub team
**To:** firmware team
**Re:** `docs/notes/2026-05-17-low-heap-power-upload-memo.md`
**Status:** Hub-side proposal for firmware-team review — **awaiting
sign-off before any hub code lands.**

## Restating the problem

Per your 2026-05-17 memo, ESP8266 Sonoff S31s `.225` and `.69`:

- Stable: `central=true, power=false` *or* `central=false, power=true`
- **Unstable**: `central=true, power=true`

Progression through `0.1.38 → 0.1.39 → 0.1.40-dev-central-safe` trimmed
response-body allocation and tightened the upload cadence, but `.69`
still rebooted with `reset_reason="Exception"` during `central+power`.
The conclusion you reached — and the data supports it — is that the
constrained ESP8266 path **cannot tolerate a separate HTTPS round-trip
to `/api/v1/device/power-samples` under current heap limits**, not
that the JSON payload itself is too big.

This memo answers your three proposed directions with a recommendation,
the rationale, and a concrete hub-side implementation outline.

## The three directions, scored

| | (A) Heartbeat-carried compact summary | (B) Lighter dedicated ingest | (C) Class-based transport policy |
|---|---|---|---|
| **Eliminates a TLS handshake?** | ✅ yes — folds into the existing heartbeat connection | ❌ no — still a second HTTPS round-trip | depends on which it picks |
| **Per-sample fidelity** | ❌ drops to 1 / heartbeat interval (~60 s) | ✅ preserved (firmware-cadence) | depends |
| **Hub-side complexity** | small (~30 LOC + new `source="heartbeat"` enum) | medium (new endpoint + parallel maintenance) | larger — a class taxonomy + per-class router |
| **Schema migration** | none (existing `DevicePowerSample` columns reused) | none–small | none |
| **Adds heartbeat-contract coupling?** | yes — optional nullable fields | no | depends |
| **Generalises to future device classes** | partial | no | yes (that's its purpose) |
| **Solves the unstable-`.225`/`.69` state** | **yes — if HTTPS handshake is the heap killer** | only if the killer is response parsing, not handshake | C-with-A: yes ; C-with-B: same as B |

## Recommendation: ship **(A)** first; structure it so **(C)** drops on
top of it later if a second device class ever needs a different policy.

### Why (A)

1. **Your data already names HTTPS round-trip as the suspect**, not JSON
   parsing. `0.1.40` cut response-body allocation and per-upload heap
   chatter and `.69` still crashed. (B) doesn't change that — it just
   trims further. (A) avoids the round-trip entirely by reusing the
   heartbeat's already-open connection.
2. **The fidelity loss is acceptable for wall-installed ESP8266 units.**
   At 1 sample/minute the hub still gets a continuous record of "device
   is on / drawing N watts now", which is all the cost-per-kWh + B16
   power-page surfaces actually need for these low-heap units. The
   high-resolution path (per-100ms / per-1s) stays available on
   roomier hardware.
3. **(C) on top of (A) is trivial later** — class-based policy is just
   "if device class is `esp8266`, the firmware uses the heartbeat
   path; otherwise the dedicated endpoint stays". We don't need to
   design the class taxonomy now to ship (A); we just need to mark
   the heartbeat-power path as opt-in so we don't change behavior on
   roomy devices.

### Why not (B) alone

(B) is the right answer only if **response-body parsing** was the
heap killer. Your three-version progression strongly suggests it
isn't — the connection-establishment overhead is what's left to trim.
A lighter endpoint reduces JSON-parse cost but keeps the TLS+TCP
state both ends. We'd ship (B), still see exceptions on `.225` /
`.69`, then have to backtrack to (A) anyway.

### Why not (C) first

It's a wrapper, not an implementation. We need to ship one transport
that actually works on ESP8266 before defining a router that picks
between transports. (C) on top of (A) is the natural endpoint
state — but starting at (C) is over-engineering for "we have two
unstable devices, every other device is fine."

## Hub-side implementation outline for (A)

**Wire change (additive, optional, backward-compat).**

Heartbeat body gains one optional field. Today's heartbeat carries
`recovery_mode`, `central_state`, `firmware_version`, `config_blob`,
etc.; we add:

```json
"power_compact": {
  "p_w":            123.4,        // float, the most recent watts reading
  "v_v":            122.7,        // optional — drop if heap-painful
  "i_ma":           1014,         // optional — same
  "i_ma_estimated": false,        // optional — per the 2026-05-15 current-semantics memo
  "i_ma_estimate":  null,         // optional — paired with above
  "source_flags":   0,            // optional — keep the existing bit-dictionary shape
  "sampled_uptime_seconds": 87421 // optional — for "when was this measured" provenance
}
```

Everything inside `power_compact` is optional except `p_w`. Firmware
sends only what it can afford. **No schema change on the hub** — these
map onto the existing `DevicePowerSample` columns we already store from
the dedicated endpoint; the only new value is `source="heartbeat"` in
the existing `source` enum.

**Hub-side code (single-session, no extra round-trip).**

`record_heartbeat()` already has the device + a session_scope. Add at
the end of the heartbeat write:

```python
power_compact = body.get("power_compact")
if isinstance(power_compact, dict) and power_compact.get("p_w") is not None:
    ingest_compact_power_sample(session, device.id, power_compact, now)
```

Where `ingest_compact_power_sample()` is a thin sibling of
`ingest_power_samples()` that writes one `DevicePowerSample` row with
`source="heartbeat"`. ~30 LOC plus tests.

**No response-body bloat.** The heartbeat response stays the current
84-byte shape (`{"ok":true,"data":{"next_poll_after_seconds":N,
"next_heartbeat_after_seconds":N}}`). The ack is implicit in the
heartbeat's 200; no separate JSON payload to parse.

**Opt-in.** A runtime-setting `power.heartbeat_path_enabled` (default
**false**) gates the hub-side read. Firmware sets `power_compact`
only on devices where it's enabled. Roomy hardware continues to use
the dedicated `/device/power-samples` endpoint with full per-sample
fidelity — zero behaviour change for `.48`-class units.

**Observability.** The `source="heartbeat"` enum value makes it
trivial to chart which devices took the compact path vs the dedicated
one, and the `/app/power` per-device timeline gracefully accepts a
mixed series.

**Tests.** Three additions:
1. Unit: `ingest_compact_power_sample` writes one row with `source="heartbeat"`, the `p_w` value lands, nullable fields handle absence.
2. Unit: `record_heartbeat` calls `ingest_compact_power_sample` exactly once per heartbeat when `power_compact` is present, never when it's absent.
3. Live: post a heartbeat with `power_compact` against the gate's app, assert one `DevicePowerSample` row appears with `source="heartbeat"`.

## Migration path (if you ever want B or full C)

- (B) becomes purely additive — a `/api/v1/device/power-samples-light`
  endpoint with `204 No Content` ack. Hub-side ~40 LOC. We ship if
  (A) turns out to be too lossy for some new use case.
- (C) becomes a one-line policy: "if device.class == 'esp8266', the
  hub *expects* the heartbeat-power path; if class == 'esp32-class' or
  unset, expect the dedicated endpoint." The class field already has
  a natural home in `Device.capabilities` or a new column; the policy
  itself is per-class, not per-device.

## What we're asking from the firmware team

1. Sign off on **(A) as the first ship**, with (B)/(C) explicitly
   deferred as fallbacks if (A) turns out to be insufficient.
2. Decide which of the optional `power_compact` sub-fields are heap-
   affordable today on `.225` / `.69`. The minimum viable shape is
   `{"p_w": …}`; everything else is welcome but optional.
3. Confirm the timing — once (A) ships hub-side (likely one ship,
   small surface), the firmware-side change is the only remaining
   blocker for moving the ESP8266 fleet back to `central=true,
   power=true`.

If you'd prefer (B) for reasons I haven't covered (e.g. heap
measurements showing response parse, not handshake, was the killer
in `0.1.40`), let's swap. The hub-side cost is roughly the same.

## What we're NOT asking

- We are **not** asking firmware to redesign the local power
  monitor. The local sample rate and accuracy are fine; (A) only
  changes the *transport* by which the latest reading reaches the
  hub.
- We are **not** asking firmware to give up the dedicated endpoint
  on roomy devices. The compact path is opt-in per device.

— hub team
