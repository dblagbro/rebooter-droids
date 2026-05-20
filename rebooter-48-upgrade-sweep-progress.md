# Rebooter 48 Upgrade Sweep Progress

## 2026-05-19

Live renamed-test recheck completed at `2026-05-19 22:35 EDT`
(`2026-05-20T02:35Z` in hub/device response timestamps).

Concrete findings:

1. The renamed-test upgrade/recovery path is still converged across the live
   hub UI, hub API, and local device APIs.
   - Hub `v0.5.102` devices list, detail page, and admin detail API all still
     show `Rebooter - renamed test` on `0.1.37-dev-central-safe`.
   - Local `/api/status` and `/api/config` still match that identity and
     firmware, with `health_state="healthy"` and `recovery_mode=false`.
   - No fresh rename drift or recovery regression was verified in this pass.

2. The concrete `.48` reliability issue is degraded power telemetry behind an
   otherwise healthy central state.
   - Hub latest power sample for `.48` is synthetic-only with null electrical
     values even while the device row remains `central_ok`.
   - Local `/api/status` reports `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`, and
     `power_invalid_frame_count=22`.
   - Protected `/api/system/heartbeat-preview` matches that same no-valid-frame
     power state.

3. This is worse than the earlier `.48` baseline.
   - [`docs/notes/2026-05-15-rebooter-48-real-cse7766-status.json`](/S:/code/rebooter-droids/docs/notes/2026-05-15-rebooter-48-real-cse7766-status.json)
     previously captured `.48` with `power_chip_seen=true`,
     `power_source="steady"`, and `power_valid_frame_count=942`.
   - Treat the current zero-valid-frame state as the main live regression on
     the renamed-test soak thread.

Follow-up capture at `2026-05-19 22:47 EDT`:
- The live rendered hub devices page and hub device-detail page still matched
  the device APIs on `Rebooter - renamed test` / `0.1.37-dev-central-safe`
  / `192.168.1.48`.
- The hub detail page still rendered `health: healthy`,
  `recovery_mode: False`, and synthetic-only power state.
- Local `/api/status` still reported `power_chip_seen=false`,
  `power_source="none"`, `power_valid_frame_count=0`, and
  `power_invalid_frame_count=22`.
- No fresh rename drift, upgrade drift, or recovery regression was verified
  beyond the standing power-telemetry failure.

Second follow-up capture at `2026-05-19 22:57 EDT`:
- The authenticated hub UI, hub admin detail API, local `/api/status`, and
  local `/api/config` still matched on
  `Rebooter - renamed test` / `0.1.37-dev-central-safe` / `192.168.1.48`.
- The main improved finding is stronger duration evidence for the same live
  failure: hub admin detail now shows a fully synthetic last-24-hour power
  window with `power_source_breakdown.real=0`,
  `power_source_breakdown.synthetic=73023`, and
  `power_source_breakdown.total=73023`.
- The `2026-05-19` hub daily rollup for `.48` is also fully synthetic with
  `sample_count=73159`, `synthetic_sample_count=73159`, and
  `is_synthetic_tainted=true`.
- Local `/api/status` and protected `/api/system/heartbeat-preview` still
  showed `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- No fresh rename drift, upgrade drift, or recovery regression was verified in
  this pass.

Third follow-up capture at `2026-05-19 23:07 EDT`:
- The live hub devices page, hub detail page, hub admin detail API, local
  `/api/status`, local `/api/config`, and protected
  `/api/system/heartbeat-preview` still matched on
  `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `recovery_mode=false`.
- The stronger improved finding is scope, not recovery: the power-telemetry
  failure is now clearly multi-day while power analytics remains enabled on
  both sides.
- Hub admin detail still showed a synthetic-only last-24-hour window with
  `power_source_breakdown.real=0`,
  `power_source_breakdown.synthetic=73021`, and
  `power_source_breakdown.total=73021`.
- Hub daily rollups now show two consecutive fully synthetic days for `.48`:
  `2026-05-19` with `sample_count=73159`,
  `synthetic_sample_count=73159`, and `2026-05-18` with
  `sample_count=73066`, `synthetic_sample_count=73066`.
- Hub `last_reported_config.power` and local `/api/config.power` both still
  showed `enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`, so this
  is not explained by power analytics being disabled.
- Local `/api/status` and protected `/api/system/heartbeat-preview` still
  reported `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- Protected `/api/system/central-diagnostic` still reached the live hub
  version endpoint with HTTP `200` and version `0.5.102`.
- No fresh rename drift, upgrade drift, or recovery regression was verified in
  this pass.

Fourth follow-up capture at `2026-05-19 23:26 EDT`:
- The live hub list/detail pages, hub admin APIs, local `/api/status`, and
  local `/api/config` still matched on
  `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `online`, `central_ok`, and
  `reported_recovery_mode=false`.
- The local device browser shell still served the same generic static page,
  and `/app.js` still carried the `state.status.device_name` hydration path
  plus `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was
  found on the device side.
- The power-telemetry failure remains the only concrete live issue and now
  spans at least four day buckets in hub rollups:
  `2026-05-19` synthetic `73159/73159`,
  `2026-05-18` synthetic `73066/73066`,
  `2026-05-17` synthetic `5511/5511`, and
  `2026-05-16` synthetic `31334/31334`.
- The hub admin detail still showed a synthetic-only 24-hour window with
  `power_source_breakdown.real=0`,
  `power_source_breakdown.synthetic=73009`, and
  `power_source_breakdown.total=73009`, while local `/api/status` still
  showed `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- One chained follow-up probe aborted once with `RemoteDisconnected`, but
  immediate isolated retries on the hub version endpoint plus device `/`,
  `/api/status`, `/api/config`, and `/app.js` all returned HTTP `200`, so
  this stays weak flake signal rather than a confirmed new regression.
- No fresh rename drift, upgrade drift, or recovery regression was verified in
  this pass.

Fifth follow-up capture at `2026-05-19 23:46 EDT`:
- The live hub list/detail pages, hub admin detail API, local `/api/status`,
  local `/api/config`, and protected `/api/system/heartbeat-preview` still
  matched on `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `online`, `central_ok`, `registration_state="active"`,
  and `reported_recovery_mode=false`.
- The local device browser shell still served the same generic static page,
  and `/app.js` still carried the `state.status.device_name` hydration path
  plus `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was
  found on the device side.
- The standing power-telemetry failure remains the only concrete live issue,
  and this pass improves the time-boundary evidence: the hub 24-hour breakdown
  is still synthetic-only with `real=0`, `synthetic=72933`, and
  `total=72933`, while daily rollups remain fully synthetic for
  `2026-05-19`, `2026-05-18`, `2026-05-17`, and `2026-05-16`.
- The older `2026-05-15` daily rollup is also now visible with
  `sample_count=4111` and `synthetic_sample_count=3603`, implying only `508`
  non-synthetic samples in that partial day and strengthening the conclusion
  that real power telemetry disappeared on `2026-05-15`.
- Local `/api/status` and protected `/api/system/heartbeat-preview` still
  showed `power_analytics_enabled=true`, `power_chip_seen=false`,
  `power_source="none"`, `power_valid_frame_count=0`, and
  `power_invalid_frame_count=22`, while hub and local config still agreed on
  `power.enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`.
- Protected `/api/system/central-diagnostic` still reached the live hub
  version endpoint with HTTP `200`, resolving `www.voipguru.org` to
  `24.168.14.36` and reporting version `0.5.102`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Sixth follow-up capture at `2026-05-20 00:01 EDT`:
- The live hub list/detail pages, hub admin detail API, local `/api/status`,
  local `/api/config`, and protected `/api/system/heartbeat-preview` still
  matched on `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and `reported_recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still carried the `state.status.device_name` hydration path plus
  `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was found
  on the device side.
- The standing power-telemetry failure remains the only concrete live issue:
  hub admin detail still showed a synthetic-only latest power sample and a
  synthetic-only 24-hour window with `real=0`, `synthetic=72920`, and
  `total=72920`, while local `/api/status` and protected
  `/api/system/heartbeat-preview` still showed `power_chip_seen=false`,
  `power_source="none"`, `power_valid_frame_count=0`, and
  `power_invalid_frame_count=22`.
- Protected `/api/system/central-diagnostic` still reached the live hub
  version endpoint with HTTP `200`, resolved `www.voipguru.org` to
  `24.168.14.36`, and reported version `0.5.102`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Seventh follow-up capture at `2026-05-20 00:07 EDT`:
- The live hub list/detail pages, hub admin detail API, local `/api/status`,
  local `/api/config`, and protected `/api/system/heartbeat-preview` still
  matched on `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and `reported_recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still carried the `state.status.device_name` hydration path plus
  `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was found
  on the device side.
- The standing power-telemetry failure remained unchanged overnight: hub admin
  detail still showed a synthetic-only latest power sample and a synthetic-only
  24-hour window with `real=0`, `synthetic=72924`, and `total=72924`, while
  local `/api/status` and protected `/api/system/heartbeat-preview` still
  showed `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- New concrete reliability regression: hub `last_reported_config.central`,
  local `/api/config.central`, and protected
  `/api/system/heartbeat-preview.reported_config.central` now all show only a
  single central base URL, `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic` probed only that one target.
- This regresses from the earlier `.48` protected-config and heartbeat
  artifacts on `2026-05-14`, which still carried both
  `https://www.voipguru.org/rebooter` and
  `https://www2.voipguru.org/rebooter`; the renamed-test soak target therefore
  currently lacks the previously documented device-side secondary-hub failover
  path.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Eighth follow-up capture at `2026-05-20 00:16 EDT`:
- The live device APIs still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with `health_state="healthy"`,
  `recovery_mode=false`, `central_registered=true`, and
  `central_state="idle"`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained the `state.status.device_name` hydration path plus
  `X-Rebooter-Auth` attach logic, so no fresh device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  local `/api/status` and protected `/api/system/heartbeat-preview` both still
  showed `power_analytics_enabled=true`, `power_chip_seen=false`,
  `power_source="none"`, `power_valid_frame_count=0`, and
  `power_invalid_frame_count=22`.
- The single-host central-config regression also still reproduced unchanged:
  local `/api/config.central.base_urls`,
  `/api/system/heartbeat-preview.reported_config.central.base_urls`, and
  protected `/api/system/central-diagnostic.targets[*].base_url` still showed
  only `https://www.voipguru.org/rebooter`, with no secondary
  `https://www2.voipguru.org/rebooter` failover target.
- Protected `/api/system/central-diagnostic` still reached the live hub
  version endpoint with HTTP `200`, resolved `www.voipguru.org` to
  `24.168.14.36`, and reported version `0.5.102`.
- This workstation could not complete a fresh direct hub-UI or hub-API scrape
  during the same pass because outbound TLS requests from the host shell failed
  with `No credentials are available in the security package`; treat that as a
  runner-environment limitation, not a new hub regression.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Ninth follow-up capture at `2026-05-20 00:27 EDT`:
- A fresh direct hub UI/API scrape succeeded from this workstation via Python
  `requests`, and the live hub list page, hub detail page, hub admin detail
  API, local `/api/status`, local `/api/config`, and protected
  `/api/system/heartbeat-preview` still matched on
  `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `online`, `central_ok`,
  `registration_state="active"`, `reported_central_state="heartbeat"`,
  local `central_state="idle"`, and `recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained the `state.status.device_name` hydration path plus
  `X-Rebooter-Auth` attach logic, so no fresh device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  hub admin detail still showed a synthetic-only latest power sample at
  `2026-05-20T04:26:47Z` and a synthetic-only current 24-hour breakdown with
  `real=0`, `synthetic=72907`, and `total=72907`, while local
  `/api/status` and protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`,
  local `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  showed only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic` still probed only that one target.
- Protected `/api/system/central-diagnostic` still reached the live hub
  version endpoint with HTTP `200`, resolved `www.voipguru.org` to
  `24.168.14.36`, and returned hub version `0.5.102`.
- Improved finding: the earlier workstation TLS blocker was limited to the
  host-shell path, not the product surface; this pass completed a direct
  authenticated hub UI/API recheck and still found no fresh rename drift,
  firmware drift, upgrade drift, or recovery regression.

Tenth follow-up capture at `2026-05-20 00:47 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`, while local `/api/status` and protected
  `/api/system/heartbeat-preview` returned to `central_state="idle"` on the
  confirmation pass.
- The local device root still served the same generic static shell, and
  `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T04:47:08Z` remained
  synthetic-only, the hub current 24-hour breakdown remained fully synthetic
  with `real=0`, `synthetic=72866`, and `total=72866`, and local
  `/api/status` plus protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The hub still preserved the same multi-day synthetic-only history for `.48`:
  daily rollups remained fully synthetic on `2026-05-19` (`73159/73159`),
  `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and
  `2026-05-16` (`31334/31334`), with only `508` non-synthetic samples left
  in the partial `2026-05-15` bucket (`4111` total / `3603` synthetic).
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`, local
  `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  contained only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic.targets[*].base_url` still listed only that
  same primary target while reaching the live hub version endpoint with HTTP
  `200` and version `0.5.102`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Twelfth follow-up capture at `2026-05-20 01:06 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T05:06:41Z` remained
  synthetic-only, the hub current 24-hour breakdown remained fully synthetic
  with `real=0`, `synthetic=72842`, and `total=72842`, and local
  `/api/status` plus protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The hub still preserved the same multi-day synthetic-only history for `.48`:
  daily rollups remained fully synthetic on `2026-05-19` (`73159/73159`),
  `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and
  `2026-05-16` (`31334/31334`), with only `508` non-synthetic samples left
  in the partial `2026-05-15` bucket (`4111` total / `3603` synthetic).
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`, local
  `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  contained only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic.targets[*].base_url` still listed only that
  same primary target while reaching the live hub version endpoint with HTTP
  `200` and version `0.5.102`.
- A brief local `central_state="heartbeat_ok"` sample did not hold up as a
  concrete improvement: an immediate 5-sample continuity loop returned both
  local `/api/status` and protected `/api/system/heartbeat-preview` to
  `central_state="idle"` on every follow-up sample while
  `central_heartbeat_age_seconds` advanced from `28` to `38`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Eleventh follow-up capture at `2026-05-20 00:57 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`, while local `/api/status` and protected
  `/api/system/heartbeat-preview` still reported `health_state="healthy"`,
  `recovery_mode=false`, `central_registered=true`, and
  local `central_state="idle"`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T04:57:01Z` remained
  synthetic-only, the hub current 24-hour breakdown remained fully synthetic
  with `real=0`, `synthetic=72866`, and `total=72866`, and local
  `/api/status` plus protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The hub still preserved the same multi-day synthetic-only history for `.48`:
  daily rollups remained fully synthetic on `2026-05-19` (`73159/73159`),
  `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and
  `2026-05-16` (`31334/31334`), with only `508` non-synthetic samples left
  in the partial `2026-05-15` bucket (`4111` total / `3603` synthetic).
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`, local
  `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  contained only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic.targets[*].base_url` still listed only that
  same primary target while reaching the live hub version endpoint with HTTP
  `200` and version `0.5.102`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Fourteenth follow-up capture at `2026-05-20 01:16 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`, while local `/api/status` and protected
  `/api/system/heartbeat-preview` still reported `health_state="healthy"`,
  `recovery_mode=false`, `central_registered=true`, and
  local `central_state="idle"`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T05:15:52Z` remained
  synthetic-only, the hub current 24-hour breakdown remained fully synthetic
  with `real=0`, `synthetic=72842`, and `total=72842`, and local
  `/api/status` plus protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The hub still preserved the same multi-day synthetic-only history for `.48`:
  daily rollups remained fully synthetic on `2026-05-19` (`73159/73159`),
  `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`), and
  `2026-05-16` (`31334/31334`), with only `508` non-synthetic samples left
  in the partial `2026-05-15` bucket (`4111` total / `3603` synthetic).
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`, local
  `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  contained only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic.targets[*].base_url` still listed only that
  same primary target while reaching the live hub version endpoint with HTTP
  `200` and version `0.5.102`.
- Improved finding: the earlier transient `central_state="heartbeat_ok"`
  still did not reproduce. An immediate 5-sample continuity loop returned
  both local `/api/status` and protected `/api/system/heartbeat-preview` as
  `central_state="idle"` on every sample while
  `central_heartbeat_age_seconds` advanced from `11` to `22`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

## 2026-05-18

This run was tooling-blocked for live soak verification. The local shell could not launch (`CreateProcessWithLogonW failed: 267`), so the prior local memo/log could not be read and the live hub/device UI comparison could not be re-run from this environment.

Concrete findings verified from the current repo documentation:

1. Enrollment token revocation drift:
   - [`https://github.com/dblagbro/rebooter-droids/blob/main/docs/ADMIN_GUIDE.md`](https://github.com/dblagbro/rebooter-droids/blob/main/docs/ADMIN_GUIDE.md) says single-use tokens cannot be revoked before redemption.
   - [`https://github.com/dblagbro/rebooter-droids/blob/main/docs/API.md`](https://github.com/dblagbro/rebooter-droids/blob/main/docs/API.md) documents `DELETE /admin/enrollment-tokens/{token_id}` for invalidating an unconsumed token.
   - Treat as a reliability/docs regression until the live UI copy and API contract are shown consistent.

2. `apply_config` schema drift:
   - [`https://github.com/dblagbro/rebooter-droids/blob/main/docs/API.md`](https://github.com/dblagbro/rebooter-droids/blob/main/docs/API.md) lists `power` as an allowed top-level key in `apply_config`.
   - [`https://github.com/dblagbro/rebooter-droids/blob/main/docs/DEVICE_INTEGRATION.md`](https://github.com/dblagbro/rebooter-droids/blob/main/docs/DEVICE_INTEGRATION.md) omits `power` from the locked top-level key list.
   - Treat as a hub/device contract drift that can cause config application mismatches.

No fresh live hub/device regressions were verified this run beyond those documented contract inconsistencies.
