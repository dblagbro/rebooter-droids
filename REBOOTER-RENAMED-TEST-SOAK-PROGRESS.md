## 2026-05-15 recheck

Scope of this recheck:
- Re-compare the published hub UI/API contract against the device integration contract for the renamed-test soak thread.
- Surface only concrete regressions, reliability risks, or improved findings.

Concrete findings:

1. API reference drift remains real and is now large enough to be reliability-relevant.
   - [`docs/API.md`](/S:/code/rebooter-droids/docs/API.md) still says `Refreshed v0.4.27`.
   - [`CHANGELOG.md`](/S:/code/rebooter-droids/CHANGELOG.md) is at `0.5.67` dated 2026-05-15.
   - The API reference already contains newer endpoints, so the version stamp is no longer trustworthy as an operator signal.

2. `apply_config` schema drift exists between the admin API contract and the firmware/device contract.
   - [`docs/API.md`](/S:/code/rebooter-droids/docs/API.md) says admin-side validation accepts top-level keys:
     `device_name, relay_restore_behavior, monitor_interval_seconds, boot_warmup_seconds, manual_button_enabled, internet, device, notifications, power`.
   - [`docs/DEVICE_INTEGRATION.md`](/S:/code/rebooter-droids/docs/DEVICE_INTEGRATION.md) documents the same list but without `power`.
   - Reliability impact: if the hub UI or operator tooling starts sending `power`, firmware built against the published device contract may ignore it or log schema drift instead of applying it.

3. Operator-facing UI documentation understates the shipped command/config surface.
   - [`docs/ADMIN_GUIDE.md`](/S:/code/rebooter-droids/docs/ADMIN_GUIDE.md) documents device-detail commands as `relay_on`, `relay_off`, `relay_toggle`, `device_restart`, `check_firmware`, and `relay_cycle`.
   - [`docs/API.md`](/S:/code/rebooter-droids/docs/API.md) and [`docs/SPEC.md`](/S:/code/rebooter-droids/docs/SPEC.md) also describe shipped `set_mode` and `apply_config` command families.
   - Improved finding: the hub/device command model is broader than the admin guide currently tells operators, so UI-vs-contract verification still has a documentation blind spot.

4. Auth/RBAC documentation is also lagging.
   - [`docs/API.md`](/S:/code/rebooter-droids/docs/API.md) documents `GET /auth/me` role values as `super_admin|admin|operator`.
   - [`CHANGELOG.md`](/S:/code/rebooter-droids/CHANGELOG.md) records `viewer` as shipped since `0.2.0`.
   - This is lower risk than the `apply_config` drift, but it is another sign the public contract docs are not fully reconciled.

What did not change in this recheck:
- No newly verified live runtime regression was confirmed from public endpoints in this session.
- No new evidence contradicted the local-first / central-additive device contract.

Recommended next focus for the soak thread:
- Treat contract reconciliation as the current blocker before interpreting any renamed-test upgrade/recovery behavior as a firmware-only issue.

## 2026-05-19 live recheck

Scope of this recheck:
- compare the live hub UI/API against the renamed-test device UI shell and
  device APIs
- surface only concrete regressions, reliability issues, or improved findings

Concrete findings:

1. No new rename drift or upgrade/recovery drift was found.
   - At `2026-05-19 22:35 EDT` / about `2026-05-20T02:35Z`, the live hub list,
     hub detail page, admin detail API, local `/api/status`, and local
     `/api/config` all still agreed on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe`.
   - The hub still showed `registration_state="active"`,
     `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`.
   - The device still showed `health_state="healthy"`,
     `recovery_mode=false`, `central_registered=true`, and
     `uptime_seconds=188170`.

2. The concrete reliability problem on `.48` is now lost power telemetry, not name sync.
   - Hub status stayed `central_ok`, but the latest hub power sample for `.48`
     was synthetic-only with null electrical fields.
   - Device `/api/status` and protected `/api/system/heartbeat-preview` both
     reported:
     `power_analytics_enabled=true`, `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`,
     `power_invalid_frame_count=22`.
   - Reliability impact: the live hub can still look healthy while the device
     has stopped producing usable CSE7766 readings.

3. The power issue is a regression from the earlier renamed-test telemetry baseline.
   - [`docs/notes/2026-05-15-rebooter-48-real-cse7766-status.json`](/S:/code/rebooter-droids/docs/notes/2026-05-15-rebooter-48-real-cse7766-status.json)
     showed `.48` with `power_chip_seen=true`,
     `power_source="steady"`, and `power_valid_frame_count=942`.
   - The current live snapshot has dropped to zero valid frames.

4. A follow-up live capture at `2026-05-19 22:47 EDT` tightened confidence
   that this is the only current live issue on the soak thread.
   - The rendered hub list and detail pages still showed
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` at
     `192.168.1.48`.
   - The hub detail page still rendered `health: healthy` and
     `recovery_mode: False`, matching local `/api/status`.
   - The hub still showed a fresh synthetic-only latest power sample while
     local `/api/status` still reported `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`, and
     `power_invalid_frame_count=22`.

5. A second follow-up live capture at `2026-05-19 22:57 EDT` strengthens the
   power-telemetry finding without changing the rename/recovery result.
   - The authenticated hub UI, hub admin detail API, local `/api/status`, and
     local `/api/config` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
     `192.168.1.48`.
   - The hub admin detail API now shows a full-day synthetic-only power window:
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=73023`, and
     `power_source_breakdown.total=73023` over the last `86400` seconds.
   - The `2026-05-19` hub daily rollup for `.48` is also fully synthetic with
     `sample_count=73159`, `synthetic_sample_count=73159`, and
     `is_synthetic_tainted=true`.
   - Local `/api/status` and protected `/api/system/heartbeat-preview` still
     showed `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.

6. A third follow-up live capture at `2026-05-19 23:07 EDT` improves the
   diagnosis without changing the rename/recovery result.
   - The live hub list/detail pages, hub admin detail API, local
     `/api/status`, local `/api/config`, and protected
     `/api/system/heartbeat-preview` all still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
     `192.168.1.48` with `recovery_mode=false`.
   - The power failure is now clearly multi-day: hub daily rollups show
     `2026-05-19` fully synthetic with `73159/73159` synthetic samples and
     `2026-05-18` fully synthetic with `73066/73066` synthetic samples.
   - The hub admin detail API still shows a synthetic-only last-24-hour window
     with `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=73021`, and
     `power_source_breakdown.total=73021`.
   - Hub `last_reported_config.power` and local `/api/config.power` both still
     report `enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`, so
     the regression is not explained by power analytics being turned off.
   - Protected `/api/system/central-diagnostic` still reaches the live hub
     version endpoint with HTTP `200` and version `0.5.102`.

7. A fourth follow-up live capture at `2026-05-19 23:26 EDT` extended the
   same result without finding a new rename or recovery regression.
   - The live hub list/detail pages, hub admin APIs, local `/api/status`, and
     local `/api/config` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
     `192.168.1.48`, with `online`, `central_ok`, and
     `reported_recovery_mode=false`.
   - The local device browser shell still served the same generic static page,
     and `/app.js` still carried the `state.status.device_name` hydration path
     plus `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift
     was found on the device side.
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
   - A single chained follow-up probe aborted once with
     `RemoteDisconnected`, but immediate isolated retries on the hub version
     endpoint plus device `/`, `/api/status`, `/api/config`, and `/app.js`
     all returned HTTP `200`, so this remains weak flake signal rather than a
     confirmed new regression.

8. A fifth follow-up live capture at `2026-05-19 23:46 EDT` improves the
   duration evidence again without changing the rename/recovery result.
   - The live hub list/detail pages, hub admin detail API, local
     `/api/status`, local `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
     `192.168.1.48`, with `online`, `central_ok`,
     `registration_state="active"`, and `reported_recovery_mode=false`.
   - The local device root still served the same generic static shell, and
     `/app.js` still carried the `state.status.device_name` hydration path
     plus `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift
     was found on the device side.
   - The hub admin detail still showed a synthetic-only latest power sample
     plus a synthetic-only 24-hour window with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72933`, and
     `power_source_breakdown.total=72933`.
   - Hub daily rollups still show fully synthetic day buckets for
     `2026-05-19` (`73159/73159`), `2026-05-18` (`73066/73066`),
     `2026-05-17` (`5511/5511`), and `2026-05-16` (`31334/31334`), and the
     older `2026-05-15` rollup is now visible with `sample_count=4111` and
     `synthetic_sample_count=3603`, implying only `508` non-synthetic samples
     in that partial day.
   - Local `/api/status` and protected `/api/system/heartbeat-preview` still
     showed `power_analytics_enabled=true`, `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`, and
     `power_invalid_frame_count=22`, while hub and local config still agreed
     on `power.enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`.
   - Protected `/api/system/central-diagnostic` still reached the live hub
     version endpoint with HTTP `200`, resolving `www.voipguru.org` to
     `24.168.14.36` and reporting version `0.5.102`.

9. A sixth follow-up live capture at `2026-05-20 00:01 EDT` extended the
   same result without finding a new rename or recovery regression.
   - The live hub list/detail pages, hub admin detail API, local
     `/api/status`, local `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
     `192.168.1.48`, with `central_ok`, `registration_state="active"`,
     `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`.
   - The local device root still served the same generic static shell, and
     `/app.js` still carried the `state.status.device_name` hydration path
     plus `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift
     was found on the device side.
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

Seventh follow-up capture at `2026-05-20 00:07 EDT`:
- The live hub list/detail pages, hub admin detail API, local `/api/status`,
  local `/api/config`, and protected `/api/system/heartbeat-preview` still
  matched on `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `online`, `central_ok`, `registration_state="active"`,
  and `reported_recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained the `state.status.device_name` hydration path plus
  `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was found.
- The standing power-telemetry failure remained unchanged overnight: hub admin
  detail still showed a synthetic-only latest power sample and a synthetic-only
  24-hour breakdown with `real=0`, `synthetic=72924`, and `total=72924`,
  while daily rollups stayed fully synthetic for `2026-05-19`,
  `2026-05-18`, `2026-05-17`, and `2026-05-16`; local `/api/status` and
  protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
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
- A fresh direct hub UI/API scrape succeeded again from this workstation via
  Python `requests`, and the live hub list page, hub detail page, hub admin
  APIs, local `/api/status`, local `/api/config`, and protected
  `/api/system/heartbeat-preview` still matched on
  `Rebooter - renamed test` / `0.1.37-dev-central-safe` /
  `192.168.1.48`, with `online`, `central_ok`,
  `registration_state="active"`, `reported_central_state="heartbeat"`,
  local `central_state="idle"`, and `recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained the `state.status.device_name` hydration path plus
  `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was found.
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
- Improved finding: the previous workstation TLS blocker was limited to the
  host-shell path, not the product surface; a direct authenticated hub UI/API
  recheck completed successfully in this pass and still found no fresh rename,
  firmware, upgrade, or recovery regression.

Tenth follow-up capture at `2026-05-20 00:41 EDT`:
- A fresh direct authenticated hub UI/API scrape plus local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with hub `online`,
  `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`, while local `/api/status` still reported
  `health_state="healthy"`, `central_registered=true`,
  `central_state="idle"`, and `recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained both the `state.status.device_name` hydration path
  and `X-Rebooter-Auth` attach logic, so no fresh browser-surface drift was
  found on the device side.
- The standing power-telemetry failure still reproduced unchanged:
  hub list/detail still showed a synthetic-only latest power sample with null
  electrical fields, hub detail still showed a synthetic-only 24-hour
  breakdown with `real=0`, `synthetic=72892`, and `total=72892`, and local
  `/api/status` plus protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The hub still exposed the same multi-day synthetic-only history for `.48`:
  `power_rollups_daily` and `GET /api/v1/admin/devices/<id>/power-rollups`
  still showed fully synthetic day buckets for `2026-05-19`
  (`73159/73159`), `2026-05-18` (`73066/73066`), `2026-05-17` (`5511/5511`),
  and `2026-05-16` (`31334/31334`), with only `508` non-synthetic samples
  left in the partial `2026-05-15` bucket (`4111` total / `3603`
  synthetic).
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`, local
  `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  showed only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic` still probed only that one target while
  reaching the live hub version endpoint with HTTP `200` and version
  `0.5.102`.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Eleventh follow-up capture at `2026-05-20 00:47 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still rendering
  the device in the active list/detail pages and still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`.
- The local device browser shell still served the same generic static page,
  and `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T04:47:08Z` was still
  synthetic-only, the hub current 24-hour breakdown was still fully synthetic
  with `real=0`, `synthetic=72866`, and `total=72866`, and local
  `/api/status` plus protected `/api/system/heartbeat-preview` still showed
  `power_chip_seen=false`, `power_source="none"`,
  `power_valid_frame_count=0`, and `power_invalid_frame_count=22`.
- The same multi-day duration evidence still held in hub daily rollups:
  `2026-05-19` remained fully synthetic (`73159/73159`),
  `2026-05-18` remained fully synthetic (`73066/73066`),
  `2026-05-17` remained fully synthetic (`5511/5511`), and
  `2026-05-16` remained fully synthetic (`31334/31334`), while the partial
  `2026-05-15` bucket still showed only `508` non-synthetic samples
  (`4111` total / `3603` synthetic).
- The single-host central-config regression also still reproduced unchanged:
  hub `last_reported_config.central.base_urls`, local
  `/api/config.central.base_urls`, and protected
  `/api/system/heartbeat-preview.reported_config.central.base_urls` still
  contained only `https://www.voipguru.org/rebooter`, and protected
  `/api/system/central-diagnostic.targets[*].base_url` still listed only that
  same primary target while reaching the live hub version endpoint with HTTP
  `200` and version `0.5.102`.
- A transient local `central_state="heartbeat_ok"` appeared in one probe, but
  an immediate targeted recheck returned both local `/api/status` and
  protected `/api/system/heartbeat-preview` to `central_state="idle"`, so
  treat that as sampling noise rather than a concrete recovery improvement.
- No fresh rename drift, firmware drift, upgrade drift, or recovery
  regression was verified in this pass.

Twelfth follow-up capture at `2026-05-20 00:57 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still rendering
  the device in the active list/detail pages and still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`, while local `/api/status` and protected
  `/api/system/heartbeat-preview` still reported `health_state="healthy"`,
  `recovery_mode=false`, `central_registered=true`, and
  local `central_state="idle"`.
- The local device browser shell still served the same generic static page,
  and `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T04:57:01Z` remained
  synthetic-only with null electrical values, the hub current 24-hour
  breakdown remained fully synthetic with `real=0`, `synthetic=72866`, and
  `total=72866`, and local `/api/status` plus protected
  `/api/system/heartbeat-preview` still showed `power_chip_seen=false`,
  `power_source="none"`, `power_valid_frame_count=0`, and
  `power_invalid_frame_count=22`.
- The same multi-day duration evidence still held in hub daily rollups:
  `2026-05-19` remained fully synthetic (`73159/73159`),
  `2026-05-18` remained fully synthetic (`73066/73066`),
  `2026-05-17` remained fully synthetic (`5511/5511`), and
  `2026-05-16` remained fully synthetic (`31334/31334`), while the partial
  `2026-05-15` bucket still showed only `508` non-synthetic samples
  (`4111` total / `3603` synthetic).
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

Thirteenth follow-up capture at `2026-05-20 01:06 EDT`:
- A fresh direct authenticated hub UI/API scrape and local device UI/API
  recheck still matched on `Rebooter - renamed test` /
  `0.1.37-dev-central-safe` / `192.168.1.48`, with the hub still rendering
  the device in the active list/detail pages and still reporting
  `online`, `central_ok`, `registration_state="active"`,
  `reported_central_state="heartbeat"`, and
  `reported_recovery_mode=false`.
- The local device root still served the same generic static shell, and
  `/app.js` still contained both the `state.status.device_name`
  hydration path and `X-Rebooter-Auth` attach logic, so no fresh
  device-browser drift was found.
- The standing power-telemetry failure still reproduced unchanged:
  the hub latest power sample at `2026-05-20T05:06:41Z` remained
  synthetic-only with null electrical values, the hub current 24-hour
  breakdown remained fully synthetic with `real=0`, `synthetic=72842`, and
  `total=72842`, and local `/api/status` plus protected
  `/api/system/heartbeat-preview` still showed `power_chip_seen=false`,
  `power_source="none"`, `power_valid_frame_count=0`, and
  `power_invalid_frame_count=22`.
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
