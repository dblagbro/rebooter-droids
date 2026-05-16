# B16 Multimodal Research Feedback

Date: 2026-05-15

## Short answer

Yes: the enhanced power analytics work is now explicitly reviewed and
backlogged on the firmware side.

What was already in flight before this research note:

- real CSE7766 telemetry on Sonoff S31
- stable power-sample upload path to the hub
- richer firmware status/recovery contract
- desired-config / reported-config alignment work

What this research note adds:

- a much broader multimodal architecture scope
- solar / Zigbee / BLE / SDR source research requests
- cross-cutting event-bus / sizing / onboarding decisions

This note is the firmware/product feedback pass to help the research team
tighten the next dev note.

---

## What is already answered or materially de-risked by current firmware work

### Power-sample contract

The firmware-side live power contract is now real, not synthetic-only:

- `v_v`
- `i_ma`
- `p_w`
- `s_va`
- `pf`
- `energy_wh`
- `sampled_uptime_seconds`
- `source`
- `source_flags`
- `chip_type`
- optional `hz` when hardware/path supports it

Bench proof on `.48` now shows real line voltage from a live outlet on
`0.1.25-dev-central-safe`.

### Current opinion on power schema direction

Do **not** try to stretch the existing power-specific sample table into a
single giant sparse table for every modality.

Recommended direction:

1. keep a common ingest envelope:
   - source id
   - device id
   - sampled time
   - modality
   - quality / source flags
   - metadata
2. keep **power** in a typed power sample store
3. add separate modality-specific raw stores, or a JSONB payload behind a
   common envelope plus modality-specific views/materializations

Reason:

- power, temperature, contact-state, occupancy, SDR meter bursts, and router
  stats do not belong in one wide sparse row model
- typed power queries are already valuable and should stay fast
- multimodal expansion should preserve query clarity and retention control

### Current opinion on internal event-bus architecture

Recommended stance for `G1`:

- **do not** force constrained device firmware to publish to MQTT
- keep direct HTTPS ingest from firmware devices
- use MQTT internally only where it is already the natural source:
  - Zigbee2MQTT
  - rtl_433
  - possibly Home Assistant / BLE sidecars

Why:

- the plug firmware already has a clean HTTPS ingest path
- adding MQTT to the plugs increases operational coupling and failure surface
- the right compromise is a mixed architecture with one normalized ingest layer,
  not one transport protocol for everything

### Current opinion on failure isolation

Recommended stance for `G4`:

- each modality adapter should be an independent service/process/container
- one source failing must degrade only that modality
- analytics fusion should operate on partial inputs when one modality is absent

This should be locked in early.

---

## Firmware-side backlog created or sharpened by this note

### High priority

1. loaded-power validation on real CSE7766 hardware
   - current bench proof is no-load only
   - next proof should use a known load so `i_ma` and `p_w` move off zero

2. power quality / sampler diagnostics exposure
   - current live status includes valid/invalid frame counts
   - we should keep these visible because UART/frame-noise quality is an
     important operational diagnostic for power analytics

3. measured time-sync characterization (`G2`)
   - do not estimate this
   - actually measure cross-device drift and NTP convergence on the supported
     ESP8266 / ESP32 families

4. power-sample retention / batching guidance input for hub sizing (`G3`)
   - firmware can help quantify realistic sample rates and batch volumes

### Medium priority

5. explicit modality/source flags contract
   - define how source quality, synthetic fallback, and partial-field validity
     should appear across modalities

6. firmware-side stance on read-only safety for solar integrations (`C3`)
   - firmware/product should explicitly support a read-only driver stance for
     any inverter integration in v1.x

7. BLE proxy timing impact measurement (`E1`)
   - especially whether BLE work perturbs 1 Hz power capture assumptions on
     ESP32-based devices

---

## Feedback to send the research team

## Priority-1 feedback

### A1-A4 Enphase

- Good priority choice.
- Strong recommendation: narrow the first support target to **firmware 7.0+**
  and **metered gateways** first.
- Do not let pre-7.0 or non-metered model coverage block the first driver note.
- For A2/A3 specifically, ask them to report:
  - **which fields materially change every second**
  - which are effectively 5-minute aggregates
  - which endpoints are useful for *live* subtraction/covariate work versus
    inventory/reporting only

That distinction matters more than “what endpoints exist.”

### C1 SunSpec simulator

- Also a good priority choice.
- Please ask them to return a **minimal recommended read set** for v1, not just
  a full model walk.
- We need:
  - minimal model IDs to poll
  - required scale-factor handling
  - read-only register subset
  - vendor-quirk flags

Otherwise the next dev note will be too broad.

### D1-D2 Zigbee coordinator + library

- Good to prioritize.
- Ask them to end with one explicit recommendation, not “tradeoffs.”
- My prior is:
  - coordinator: **SLZB-06MG24** if budget allows and central placement matters
  - stack: **Zigbee2MQTT** over ZHA for device breadth and easier externalized
    integration, unless their 24-hour ops test clearly disproves that

### G1 Event bus

- Research team should not assume a universal MQTT rewrite.
- Ask them to compare:
  - “heterogeneous transports + normalized ingest”
  - vs “MQTT everywhere”
- My recommendation today is the first option.

### G3 Hub sizing

- Ask them to split sizing into:
  - core power-only
  - power + solar
  - full multimodal (Zigbee + BLE + SDR + solar)
- One sizing answer will be too muddy.

Suggested target recommendation shape:

- entry-level
- recommended
- full-featured / advanced

## Priority-2 feedback

### B Tesla

- Keep this as feasibility, not a blocker.
- If local access is eroding, the note should explicitly plan for:
  - local driver
  - cloud fallback driver
- Do not hide those behind one “Tesla works” statement until proven.

### D5 Zigbee schema impact

- Important and should not be hand-waved.
- Please push them away from “one raw_samples table with one column per
  phenomenon.”
- Ask for a concrete schema proposal with retention implications.

### E BLE

- Worth researching, but do not let BLE proxy work destabilize existing
  primary power-monitoring responsibilities on ESP32 plugs.
- The key question is not just “does bluetooth_proxy run,” but:
  - what does it do to timing jitter
  - memory headroom
  - background bandwidth
  - sampling stability

### I Existing data sources

- This should move up in importance.
- Home Assistant, router telemetry, and managed-switch telemetry are probably
  the **highest leverage zero-hardware-cost covariates** in the whole note.
- Those may beat Zigbee/BLE/SDR in near-term product value.

## Priority-3 feedback

### F SDR

- Keep it as advanced / opt-in.
- Good research path, but not v1 core.
- Product/legal stance should be settled before any implementation note tries
  to normalize it.

### H Whisker Labs / Ting

- Positioning should be **complementary, not competitive**.
- We should explicitly avoid pretending our hardware can compete on
  arc-detection-grade sampling rates.

---

## Draft section-by-section status summary for the research team

This is the shape I would send back right now:

- `A1-A4`: PARTIAL
  - high priority, good direction, needs narrowed support target and live capture
- `A5-A6`: PARTIAL
  - useful but should not block first Enphase driver note
- `B1-B3`: PARTIAL
  - feasibility only, not blocking
- `C1`: DONE as priority choice, PARTIAL on execution until simulator results exist
- `C2-C3`: PARTIAL
  - explicitly lock read-only stance in v1.x
- `D1-D2`: PARTIAL
  - priority is right; require explicit recommendation output
- `D3-D5`: PARTIAL
  - important follow-ons; especially schema impact
- `E1-E5`: PARTIAL
  - useful, but power-monitoring stability must remain first-class
- `F1-F5`: PARTIAL
  - advanced-path research, not core blocker
- `G1`: DONE from firmware recommendation standpoint
  - mixed transports, normalized ingest
- `G2`: BLOCKED pending actual measurement
- `G3`: PARTIAL
  - split into tiered sizing recommendations
- `G4`: DONE from architecture recommendation standpoint
  - independent adapters / graceful degradation
- `G5`: DONE from product-shape recommendation standpoint
  - power first, solar second, existing-platform integrations next, advanced
    modalities later
- `H1-H2`: DONE at recommendation level
  - complementary positioning
- `I1-I4`: PARTIAL but should be elevated
  - especially Home Assistant / router / switch telemetry

---

## Recommended response back to the research team

1. keep Priority 1 as the critical path
2. elevate `I1-I4` above most BLE/SDR work
3. require explicit recommendations, not only captured facts, for:
   - D1/D2
   - G1
   - G3
4. narrow Enphase first-pass scope to:
   - 7.0+ auth model
   - metered gateways
5. lock SunSpec v1 to read-only
6. avoid a single giant sparse multimodal sample table
