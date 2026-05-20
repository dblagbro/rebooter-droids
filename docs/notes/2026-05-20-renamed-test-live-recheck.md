# Rebooter - renamed test live recheck - 2026-05-20

Twelfth follow-up capture:
- local run time: `2026-05-20 00:57 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T04:57Z`

Concrete findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - direct authenticated fetches of live
     `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin list/detail still reported `online`, `central_ok`,
     `registration_state="active"`, `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`
   - local `/api/status`, `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` with
     `health_state="healthy"`, `recovery_mode=false`,
     `central_registered=true`, and local `central_state="idle"`
   - the local device root still served the same generic static shell, and
     `/app.js` still contained both the `state.status.device_name`
     hydration path and `X-Rebooter-Auth` attach logic, so there is still no
     fresh browser-surface drift on the device side

2. The standing power-telemetry failure remains unchanged.
   - hub latest power sample at `2026-05-20T04:57:01Z` was still
     `source_kind="synthetic"` with null electrical values
   - the hub 24-hour breakdown remained synthetic-only with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72866`, and
     `power_source_breakdown.total=72866`
   - local `/api/status` and protected `/api/system/heartbeat-preview` still
     reported `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`
   - hub detail `power_rollups_daily` still showed fully synthetic day buckets
     for `2026-05-19` (`73159/73159`), `2026-05-18` (`73066/73066`),
     `2026-05-17` (`5511/5511`), and `2026-05-16` (`31334/31334`), while the
     partial `2026-05-15` bucket still showed only `508` non-synthetic
     samples (`4111` total / `3603` synthetic)

3. The single-host central-config regression also remains unchanged.
   - hub `last_reported_config.central.base_urls`, local
     `/api/config.central.base_urls`, and protected
     `/api/system/heartbeat-preview.reported_config.central.base_urls` still
     contained only `https://www.voipguru.org/rebooter`
   - protected `/api/system/central-diagnostic.targets[*].base_url` still
     listed only the primary `www.voipguru.org` target during this pass
   - protected `/api/system/central-diagnostic` still reached
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolved `www.voipguru.org` to `24.168.14.36`, and returned hub version
     `0.5.102`

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this twelfth follow-up capture.

Thirteenth follow-up capture:
- local run time: `2026-05-20 01:06 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T05:06Z`

Concrete findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - direct authenticated fetches of live
     `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin list/detail still reported `online`, `central_ok`,
     `registration_state="active"`, `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`
   - local `/api/status`, `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` with
     `health_state="healthy"`, `recovery_mode=false`, and
     `central_registered=true`
   - the local device root still served the same generic static shell, and
     `/app.js` still contained both the `state.status.device_name`
     hydration path and `X-Rebooter-Auth` attach logic, so there is still no
     fresh browser-surface drift on the device side

2. The standing power-telemetry failure remains unchanged with slightly newer
   hub evidence.
   - hub latest power sample at `2026-05-20T05:06:41Z` was still
     `source_kind="synthetic"` with null electrical values
   - the hub 24-hour breakdown remained synthetic-only with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72842`, and
     `power_source_breakdown.total=72842`
   - local `/api/status` and protected `/api/system/heartbeat-preview` still
     reported `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`
   - hub detail `power_rollups_daily` and
     `GET /api/v1/admin/devices/<id>/power-rollups` still showed fully
     synthetic day buckets for `2026-05-19` (`73159/73159`),
     `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and
     `2026-05-16` (`31334/31334`), while the partial `2026-05-15` bucket
     still showed only `508` non-synthetic samples (`4111` total /
     `3603` synthetic)

3. The single-host central-config regression also remains unchanged.
   - hub `last_reported_config.central.base_urls`, local
     `/api/config.central.base_urls`, and protected
     `/api/system/heartbeat-preview.reported_config.central.base_urls` still
     contained only `https://www.voipguru.org/rebooter`
   - protected `/api/system/central-diagnostic.targets[*].base_url` still
     listed only the primary `www.voipguru.org` target during this pass
   - protected `/api/system/central-diagnostic` still reached
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolved `www.voipguru.org` to `24.168.14.36`, and returned hub version
     `0.5.102`

4. A brief local `central_state="heartbeat_ok"` sample still does not hold up
   as a concrete recovery improvement.
   - one probe in this pass returned local `/api/status` and protected
     `/api/system/heartbeat-preview` as `central_state="heartbeat_ok"`
   - an immediate 5-sample continuity loop returned both endpoints to
     `central_state="idle"` across all five follow-up samples while
     `central_heartbeat_age_seconds` advanced from `28` to `38`
   - treat the brief `heartbeat_ok` value as timing noise rather than a real
     state change

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this thirteenth follow-up capture.

Fourteenth follow-up capture:
- local run time: `2026-05-20 01:16 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T05:16Z`

Concrete findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - direct authenticated fetches of live
     `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin list/detail still reported `online`, `central_ok`,
     `registration_state="active"`, `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`
   - local `/api/status`, `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` with
     `health_state="healthy"`, `recovery_mode=false`,
     `central_registered=true`, and local `central_state="idle"`
   - the local device root still served the same generic static shell, and
     `/app.js` still contained both the `state.status.device_name`
     hydration path and `X-Rebooter-Auth` attach logic, so there is still no
     fresh browser-surface drift on the device side

2. The standing power-telemetry failure remains unchanged with slightly newer
   hub evidence.
   - hub latest power sample at `2026-05-20T05:15:52Z` was still
     `source_kind="synthetic"` with null electrical values
   - the hub 24-hour breakdown remained synthetic-only with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72842`, and
     `power_source_breakdown.total=72842`
   - local `/api/status` and protected `/api/system/heartbeat-preview` still
     reported `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`
   - hub detail `power_rollups_daily` and
     `GET /api/v1/admin/devices/<id>/power-rollups` still showed fully
     synthetic day buckets for `2026-05-19` (`73159/73159`),
     `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and
     `2026-05-16` (`31334/31334`), while the partial `2026-05-15` bucket
     still showed only `508` non-synthetic samples (`4111` total /
     `3603` synthetic)

3. The single-host central-config regression also remains unchanged.
   - hub `last_reported_config.central.base_urls`, local
     `/api/config.central.base_urls`, and protected
     `/api/system/heartbeat-preview.reported_config.central.base_urls` still
     contained only `https://www.voipguru.org/rebooter`
   - protected `/api/system/central-diagnostic.targets[*].base_url` still
     listed only the primary `www.voipguru.org` target during this pass
   - protected `/api/system/central-diagnostic` still reached
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolved `www.voipguru.org` to `24.168.14.36`, and returned hub version
     `0.5.102`

4. Improved finding: the earlier transient `heartbeat_ok` sample still does
   not reproduce.
   - an immediate 5-sample continuity loop after this pass returned both
     local `/api/status` and protected `/api/system/heartbeat-preview` as
     `central_state="idle"` on all five samples
   - `central_heartbeat_age_seconds` advanced cleanly from `11` to `22`
     during that loop while `uptime_seconds` advanced from `197824` to
     `197835`
   - treat the earlier `heartbeat_ok` value as timing noise rather than a
     concrete recovery improvement

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this fourteenth follow-up capture.
