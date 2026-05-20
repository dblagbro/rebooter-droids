# Rebooter 48 Upgrade Sweep Addendum - 2026-05-20 01:31 EDT

Concrete findings:

1. The live hub UI/API and reachable device UI/API surfaces still matched on `Rebooter - renamed test` / `0.1.37-dev-central-safe` / `192.168.1.48`.
   - Hub UI pages `/app/devices?status=active&search=renamed` and `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` both rendered the same name, firmware, and IP.
   - Hub admin APIs still reported `online`, `central_ok`, `registration_state="active"`, `reported_central_state="heartbeat"`, and `reported_recovery_mode=false`.
   - Local `/api/status`, `/api/config`, and protected `/api/system/heartbeat-preview` still matched the same identity and showed `health_state="healthy"`, `recovery_mode=false`, and `central_registered=true`.
   - The device root still served the same generic static shell, and `/app.js` still contained the `state.status.device_name` hydration path plus `X-Rebooter-Auth` attach logic.

2. The standing power-telemetry regression still reproduced unchanged with newer timestamps.
   - Hub latest power sample at `2026-05-20T05:31:36Z` remained `source_kind="synthetic"` with null electrical values.
   - Hub `power_source_breakdown` remained fully synthetic: `real=0`, `synthetic=72822`, `total=72822`.
   - Local `/api/status` and protected `/api/system/heartbeat-preview` still showed `power_chip_seen=false`, `power_source="none"`, `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
   - Hub daily rollups still showed fully synthetic buckets on `2026-05-19` (`73159/73159`), `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and `2026-05-16` (`31334/31334`), with only `508` non-synthetic samples left in the partial `2026-05-15` bucket (`4111` total / `3603` synthetic).

3. The single-host central-config regression still reproduced unchanged.
   - Hub `last_reported_config.central.base_urls`, local `/api/config.central.base_urls`, and protected `/api/system/heartbeat-preview.reported_config.central.base_urls` still contained only `https://www.voipguru.org/rebooter`.
   - Protected `/api/system/central-diagnostic.targets[*].base_url` still listed only the primary `www.voipguru.org` target and reached `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`, resolved IP `24.168.14.36`, and hub version `0.5.102`.

4. Improved finding: `central_state="heartbeat_ok"` now persisted across an immediate continuity loop instead of collapsing back to `idle`.
   - The initial point-in-time device fetch still saw local `/api/status` and protected `/api/system/heartbeat-preview` at `central_state="idle"`.
   - The immediate 5-sample continuity loop then switched both endpoints to `central_state="heartbeat_ok"` on sample 1 and kept them there through sample 4.
   - During that held interval, `central_heartbeat_age_seconds` advanced from `0` to `7`, and `uptime_seconds` advanced from `198717` to `198724`.
   - Treat this as improved device-side central continuity, not as a new hub/device drift, because the hub still reported `reported_central_state="heartbeat"` during the same pass.

No fresh rename drift, firmware drift, upgrade drift, or recovery regression was verified in this pass.

Note: the existing memo files on the share were not writable in place from this session, so this addendum was written as a sibling artifact instead.
