# B16 Multimodal Research Response Disposition

Date: 2026-05-15

This note captures the firmware/product-side disposition of the analytics
research team's follow-up response so we do not lose the sharpened priorities
and explicit pushes.

## Accepted as-is

These points improve the plan and should be treated as accepted guidance:

1. Tighten Priority 1 delivery scope
   - Enphase first pass: firmware `7.0+` metered gateways only
   - SunSpec first pass: minimum viable register/model set, not full coverage
   - all Priority 1 items must end with an explicit recommendation

2. Reorder by value-per-dollar, not just technical possibility
   - zero-hardware-cost sources move up:
     - solar where already installed
     - router telemetry
     - managed-switch telemetry
     - Home Assistant bridge
   - Zigbee / BLE / SDR remain valuable, but later unless they are effectively
     free by virtue of existing hardware already on site

3. Keep direct HTTPS ingest for plugs
   - no forced MQTT in constrained plug firmware
   - hub should support both:
     - HTTP ingest for firmware devices
     - MQTT subscription where the source natively lives there

4. Schema direction
   - common ingest envelope
   - modality-specific physical stores
   - avoid one giant sparse multimodal sample table

5. SunSpec v1.x stance
   - read-only only
   - no inverter-control writes exposed in the driver API surface

6. SDR / Ting positioning
   - SDR as advanced / opt-in
   - Ting / Whisker Labs as complementary, not competitive

## Explicit follow-up / escalation items

These are the points that should not get softened or quietly dropped:

### 1. Cross-modal correlation must be designed for early

Accepted:
- common envelope
- modality-specific stores

Escalation:
- the cross-modal query layer should not be treated as an afterthought
- we should preserve room for:
  - a cross-modal materialized view or equivalent fast time-bucket path
  - common query patterns for:
    - point-in-time multimodal lookup
    - windowed multimodal correlation
    - change-detection across modalities

This is not a v1.0 implementation demand, but it **is** a schema-review
requirement before the storage shape is considered settled.

### 2. G2 time synchronization must be measured, not estimated

This is now a first-class firmware research task.

Requested empirical measurement:
- at least three devices on a common LAN
- one CSE7766-class plug
- one ESP32-C3 / alternate plug path
- one Shelly path if available
- report timestamp drift / variance relative to the hub over a meaningful run

Reason:
- this determines whether several phase-locked or tight-window multimodal
  analytics are even realistic

### 3. A4 Enphase PLC link-quality discovery must be treated as a real research deliverable

Do not let this silently fall out of scope just because it is more exploratory.

Required outcome:
- either a captured local/UI-backed endpoint and schema
- or a documented "we investigated and could not surface it locally" result

### 4. E5 Theengs / free BLE covariates should be elevated

This is no longer a low-priority curiosity.

Reason:
- if users already have BLE sensors, this could unlock environmental covariates
  with effectively zero added hardware cost
- that puts it closer to the same value tier as HA/router/switch telemetry than
  to the higher-friction SDR path

### 5. Real CSE7766 data should now inform the next analytics note

The firmware state changed materially:
- live CSE7766 telemetry on `.48` is now real, not synthetic-only
- next analytics/dev-note work should use this fact explicitly

Firmware-side asks flowing from that:
- capture ~24 hours of mixed-load real data
- characterize noise and jitter
- confirm the actual atomic-snapshot behavior rather than only the intended one

## Concrete messages to send back to the research team

1. Most of their refinements are accepted and improve the design:
   - Priority 1 scope tightening
   - zero-hardware-cost source elevation
   - direct HTTPS for plugs
   - modality-specific schema
   - SunSpec read-only stance
   - SDR/Ting positioning

2. Two things need explicit follow-up before lock-in:
   - G2 time-sync measurement
   - cross-modal view/materialization path as a first-class future use case

3. Three exploratory items should not be silently dropped:
   - `A4` Enphase PLC link-quality discovery
   - `E5` Theengs / free BLE covariates
   - real empirical timing work in `G2`

4. The next dev note should explicitly acknowledge the changed firmware baseline:
   - real live CSE7766 telemetry exists now
   - analytics can start reasoning from real noise/jitter/load traces, not only
     synthetic or theoretical assumptions
