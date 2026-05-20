# Renamed-test live recheck - 2026-05-19

Scope:
- compare the live `https://www.voipguru.org/rebooter` hub UI/API against the
  renamed-test device local UI shell and device APIs on `http://192.168.1.48`
- surface only concrete regressions, reliability issues, or improved findings

Time of capture:
- local run time: `2026-05-19 22:35 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T02:35Z`
- hub version endpoint: `v0.5.102`

Concrete findings:

1. Name, firmware, and recovery state still match end-to-end after the longer soak window.
   - hub devices list, hub detail page, hub admin API detail, local
     `/api/status`, and local `/api/config` all still identify the device as
     `Rebooter - renamed test`
   - hub and device both report firmware `0.1.37-dev-central-safe`
   - hub detail still reports `registration_state="active"`,
     `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`
   - local `/api/status` reports `health_state="healthy"`,
     `recovery_mode=false`, `central_registered=true`, and
     `uptime_seconds=188170`
   - local protected `/api/system/central-diagnostic` still reaches
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`

2. Power telemetry on `.48` is now concretely degraded even though central health stays green.
   - hub list/detail both keep the device at `central_ok`, but the latest power
     sample is only synthetic:
     `source_kind="synthetic"` with `p_w=null`, `v_v=null`, `pf=null`
   - local `/api/status` reports:
     `power_analytics_enabled=true`, `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`,
     `power_invalid_frame_count=22`
   - local protected `/api/system/heartbeat-preview` matches that same
     no-sensor/no-valid-frame power state
   - this is a real reliability issue because hub central status remains healthy
     while the device has stopped producing usable power telemetry

3. This is a regression relative to the earlier `.48` power baseline, not just a cosmetic status change.
   - [`docs/notes/2026-05-15-rebooter-48-real-cse7766-status.json`](/S:/code/rebooter-droids/docs/notes/2026-05-15-rebooter-48-real-cse7766-status.json)
     captured the same renamed-test device with
     `power_chip_seen=true`, `power_source="steady"`,
     `power_valid_frame_count=942`, and `power_invalid_frame_count=507`
   - the current live snapshot has fallen from mixed-but-real telemetry to zero
     valid power frames

No fresh rename drift or upgrade/recovery regression was observed in this pass.

Follow-up capture:
- local run time: `2026-05-19 22:47 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T02:47Z`

Concrete follow-up findings:

1. The rendered hub UI still matches the device identity and recovery state.
   - form-authenticated live `/app/devices?status=active&search=renamed`
     and `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` both still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - the hub detail page still rendered `health: healthy` and
     `recovery_mode: False`, matching local `/api/status`

2. The power-telemetry regression is still present and not just an API-view artifact.
   - hub admin detail API still reported `central_status="central_ok"`,
     `reported_central_state="heartbeat"`, and a fresh
     `latest_power_sample.source_kind="synthetic"` with null electrical values
   - local `/api/status` still reported `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`,
     `power_invalid_frame_count=22`, and `uptime_seconds=188843`

No additional regression beyond the standing power-telemetry failure was
verified in this follow-up capture.

Second follow-up capture:
- local run time: `2026-05-19 22:57 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T02:57Z`

Concrete second follow-up findings:

1. The authenticated hub UI and admin API still match the renamed-test device
   identity and recovery state.
   - live `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin API still reported `central_status="central_ok"`,
     `registration_state="active"`, and
     `reported_recovery_mode=false`
   - local `/api/status` and `/api/config` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` with
     `health_state="healthy"` and `recovery_mode=false`

2. The stronger live improvement is confidence, not recovery: `.48` has now
   been synthetic-only on the hub side for roughly a full day.
   - hub admin detail API now shows
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=73023`, and
     `power_source_breakdown.total=73023` over the last `86400` seconds
   - hub daily rollup for `2026-05-19` shows
     `sample_count=73159`, `synthetic_sample_count=73159`, and
     `is_synthetic_tainted=true`
   - local `/api/status` still reports `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`, and
     `power_invalid_frame_count=22`
   - local protected `/api/system/heartbeat-preview` still matches that same
     no-valid-frame power state

No fresh rename drift, upgrade drift, or recovery regression was observed in
this second follow-up capture.

Third follow-up capture:
- local run time: `2026-05-19 23:07 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T03:07Z`

Concrete third follow-up findings:

1. The live hub UI and APIs still match the renamed-test device identity,
   firmware, and recovery state.
   - live `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin API still reported `central_status="central_ok"`,
     `registration_state="active"`, and
     `reported_recovery_mode=false`
   - local `/api/status`, `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on the same device name,
     firmware, and `recovery_mode=false`

2. The standing power-telemetry failure is now confirmed as a multi-day issue,
   not a one-day blip and not a disabled-config case.
   - hub admin detail still reports a synthetic-only latest power sample and a
     synthetic-only last-24-hour window with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=73021`, and
     `power_source_breakdown.total=73021`
   - hub daily rollups now show two consecutive fully synthetic days for `.48`:
     `2026-05-19` with `73159/73159` synthetic samples and `2026-05-18` with
     `73066/73066` synthetic samples
   - hub `last_reported_config.power` and local `/api/config.power` both still
     show `enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`
   - local `/api/status` and protected `/api/system/heartbeat-preview` still
     report `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`

3. Central reachability still looks healthy from the device side.
   - protected `/api/system/central-diagnostic` still reaches
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`
     and hub version `0.5.102`

No fresh rename drift, upgrade drift, or recovery regression was observed in
this third follow-up capture.

Fourth follow-up capture:
- local run time: `2026-05-19 23:26 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T03:26Z`

Concrete fourth follow-up findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - live `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin list/detail API still reported `online`,
     `central_ok`, `registration_state="active"`, and
     `reported_recovery_mode=false`
   - local `/api/status` and `/api/config` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` with
     `health_state="healthy"` and `recovery_mode=false`
   - the local device root still serves the generic static shell while
     `/app.js` still contains the same `state.status.device_name`
     hydration path and `X-Rebooter-Auth` attach logic, so there is still no
     fresh browser-surface drift on the device side

2. The standing power-telemetry failure remains the only concrete live issue,
   with stronger duration evidence than the earlier `23:07 EDT` pass.
   - hub admin detail still reports a synthetic-only current window with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=73009`, and
     `power_source_breakdown.total=73009` over the last `86400` seconds
   - hub daily rollups now show the failure spans at least four day buckets:
     `2026-05-19` synthetic `73159/73159`,
     `2026-05-18` synthetic `73066/73066`,
     `2026-05-17` synthetic `5511/5511`, and
     `2026-05-16` synthetic `31334/31334`
   - local `/api/status` still reports `power_analytics_enabled=true`,
     `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`
   - hub `last_reported_config.power` and local `/api/config.power` still
     agree on `enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`, so
     the continued telemetry loss is still not explained by disabled config

3. A one-off follow-up probe in this session aborted once with
   `RemoteDisconnected`, but immediate single-endpoint retries on the hub
   version endpoint, device root, `/api/status`, `/api/config`, and `/app.js`
   all returned HTTP `200`.
   - treat that as a weak transport flake signal only, not a confirmed new
     regression, because it did not reproduce on the next isolated pass

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this fourth follow-up capture.

Fifth follow-up capture:
- local run time: `2026-05-19 23:46 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T03:46Z`

Concrete fifth follow-up findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - live `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin detail still reported `online`, `central_ok`,
     `registration_state="active"`, `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`
   - local `/api/status`, `/api/config`, and protected
     `/api/system/heartbeat-preview` still matched on
     `Rebooter - renamed test` / `0.1.37-dev-central-safe` with
     `health_state="healthy"`, `recovery_mode=false`, and
     `central_registered=true`
   - the local device root still serves the generic static shell while
     `/app.js` still contains the same `state.status.device_name`
     hydration path and `X-Rebooter-Auth` attach logic, so there is still no
     fresh browser-surface drift on the device side

2. The standing power-telemetry failure remains the only concrete live issue,
   and this pass narrows the last known mixed-telemetry day.
   - hub admin detail still reported a synthetic-only latest power sample with
     null electrical values plus a synthetic-only current 24-hour window with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72933`, and
     `power_source_breakdown.total=72933`
   - hub daily rollups still show fully synthetic day buckets for
     `2026-05-19` (`73159/73159`), `2026-05-18` (`73066/73066`),
     `2026-05-17` (`5511/5511`), and `2026-05-16` (`31334/31334`)
   - the older `2026-05-15` rollup is now visible too at
     `sample_count=4111` and `synthetic_sample_count=3603`, implying only
     `508` non-synthetic samples in that partial day and strengthening the
     conclusion that real power telemetry has been gone since `2026-05-15`
   - local `/api/status` and protected `/api/system/heartbeat-preview` still
     report `power_analytics_enabled=true`, `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`, and
     `power_invalid_frame_count=22`
   - hub `last_reported_config.power` and local `/api/config.power` still
     agree on `enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`, so
     the continued telemetry loss is still not explained by disabled config

3. Central reachability still looks healthy from the device side.
   - protected `/api/system/central-diagnostic` still reaches
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolves `www.voipguru.org` to `24.168.14.36`, and reports hub version
     `0.5.102`

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this fifth follow-up capture.

Sixth follow-up capture:
- local run time: `2026-05-20 00:01 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T04:01Z`

Concrete sixth follow-up findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - live `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin detail still reported `central_ok`,
     `registration_state="active"`, `reported_central_state="heartbeat"`, and
     `reported_recovery_mode=false`
   - local `/api/status` still reported
     `device_name="Rebooter - renamed test"`,
     `firmware_version="0.1.37-dev-central-safe"`,
     `health_state="healthy"`, `recovery_mode=false`,
     `central_registered=true`, and `uptime_seconds=193332`
   - local `/api/config` still reported
     `device_name="Rebooter - renamed test"` and
     `power.enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`
   - the local device root still serves the generic static shell while
     `/app.js` still contains the same `state.status.device_name`
     hydration path and `X-Rebooter-Auth` attach logic, so there is still no
     fresh browser-surface drift on the device side

2. The standing power-telemetry failure remains the only concrete live issue.
   - hub admin detail still reported a synthetic-only latest power sample with
     null electrical values
   - the hub 24-hour breakdown remained synthetic-only with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72920`, and
     `power_source_breakdown.total=72920`
   - local `/api/status` still reported `power_analytics_enabled=true`,
     `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`
   - local protected `/api/system/heartbeat-preview` still matched that same
     no-valid-frame power state

3. Central reachability still looks healthy from the device side.
   - protected `/api/system/central-diagnostic` still reached
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolved `www.voipguru.org` to `24.168.14.36`, and reported hub version
     `0.5.102`

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this sixth follow-up capture.

Seventh follow-up capture:
- local run time: `2026-05-20 00:07 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T04:07Z`

Concrete seventh follow-up findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - live `/app/devices?status=active&search=renamed` and
     `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin detail still reported `online`, `central_ok`,
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

2. The standing power-telemetry failure remains unchanged overnight.
   - hub admin detail still reported a synthetic-only latest power sample with
     null electrical values plus a synthetic-only current 24-hour window with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72924`, and
     `power_source_breakdown.total=72924`
   - hub daily rollups remained unchanged: fully synthetic on
     `2026-05-19` (`73159/73159`), `2026-05-18` (`73066/73066`),
     `2026-05-17` (`5511/5511`), and `2026-05-16` (`31334/31334`), with the
     partial `2026-05-15` bucket still showing only `508` non-synthetic
     samples (`4111` total, `3603` synthetic)
   - local `/api/status` and protected `/api/system/heartbeat-preview` still
     reported `power_analytics_enabled=true`, `power_chip_seen=false`,
     `power_source="none"`, `power_valid_frame_count=0`, and
     `power_invalid_frame_count=22`, while hub and local config still agreed
     on `power.enabled=true`, `sample_rate_hz=1`, and `batch_seconds=10`

3. New concrete reliability regression: `.48` no longer carries the documented
   dual-hub fallback list in its live central config.
   - local `/api/config.central.base_urls`, protected
     `/api/system/heartbeat-preview.reported_config.central.base_urls`, and
     hub `last_reported_config.central.base_urls` now all contain only
     `https://www.voipguru.org/rebooter`
   - protected `/api/system/central-diagnostic` likewise probed only the
     primary `www.voipguru.org` target during this pass
   - this is a regression versus earlier `.48` protected-config and heartbeat
     artifacts from `2026-05-14`, which still carried both
     `https://www.voipguru.org/rebooter` and
     `https://www2.voipguru.org/rebooter`
   - reliability impact: the renamed-test soak target is currently missing the
     previously documented secondary-hub fallback, so a primary-hub outage
     would now have no device-side URL failover path to exercise

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this seventh follow-up capture.

Ninth follow-up capture:
- local run time: `2026-05-20 00:27 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T04:26Z`

Concrete ninth follow-up findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - direct authenticated fetches of live `/app/devices?status=active&search=renamed`
     and `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
     `Rebooter - renamed test`, `0.1.37-dev-central-safe`, and
     `192.168.1.48`
   - hub admin detail still reported `online`, `central_ok`,
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
   - hub admin detail still reported a synthetic-only latest power sample at
     `2026-05-20T04:26:47Z` with null electrical values plus a synthetic-only
     current 24-hour window with `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72907`, and
     `power_source_breakdown.total=72907`
   - local `/api/status` still reported `power_analytics_enabled=true`,
     `power_chip_seen=false`, `power_source="none"`,
     `power_valid_frame_count=0`, and `power_invalid_frame_count=22`
   - local protected `/api/system/heartbeat-preview` still matched that same
     no-valid-frame power state

3. The single-host central-config regression also remains unchanged.
   - hub `last_reported_config.central.base_urls`, local
     `/api/config.central.base_urls`, and protected
     `/api/system/heartbeat-preview.reported_config.central.base_urls` still
     contain only `https://www.voipguru.org/rebooter`
   - protected `/api/system/central-diagnostic` likewise still probed only the
     primary `www.voipguru.org` target during this pass
   - protected `/api/system/central-diagnostic` still reached
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolved `www.voipguru.org` to `24.168.14.36`, and returned hub version
     `0.5.102`

4. Improved finding: the previous workstation TLS blocker was environmental,
   not a live hub regression.
   - the earlier host-shell TLS failure was bypassed in this pass by using
     Python `requests`
   - the direct authenticated hub UI/API recheck completed successfully and
     still found no fresh rename drift, firmware drift, upgrade drift, or
     recovery regression

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this ninth follow-up capture.

Tenth follow-up capture:
- local run time: `2026-05-20 00:41 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T04:41Z`

Concrete tenth follow-up findings:

1. The live hub UI/API and reachable device UI/API surfaces still match on the
   renamed-test identity, firmware, and recovery state.
   - direct authenticated fetches of live `/app/devices?status=active&search=renamed`
     and `/app/devices/dev_01KRHTH2DQSTH1PAXBJD9P2XFY` still rendered
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

2. The standing power-telemetry failure remains unchanged, and the current hub
   payload still preserves the same multi-day duration evidence.
   - hub list/detail still reported a synthetic-only latest power sample with
     null electrical values
   - hub detail still reported a synthetic-only current 24-hour window with
     `power_source_breakdown.real=0`,
     `power_source_breakdown.synthetic=72892`, and
     `power_source_breakdown.total=72892`
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
   - protected `/api/system/central-diagnostic` likewise still probed only the
     primary `www.voipguru.org` target during this pass
   - protected `/api/system/central-diagnostic` still reached
     `https://www.voipguru.org/rebooter/api/v1/version` with HTTP `200`,
     resolved `www.voipguru.org` to `24.168.14.36`, and returned hub version
     `0.5.102`

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this tenth follow-up capture.

Eleventh follow-up capture:
- local run time: `2026-05-20 00:47 EDT`
- hub/device timestamps observed in responses: about `2026-05-20T04:47Z`

Concrete eleventh follow-up findings:

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

2. The standing power-telemetry failure remains unchanged, with slightly newer
   live evidence.
   - hub latest power sample at `2026-05-20T04:47:08Z` was still
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

4. A transient local central-state value did not hold up as a real
   improvement.
   - one probe returned local `central_state="heartbeat_ok"`
   - an immediate targeted recheck returned both local `/api/status` and
     protected `/api/system/heartbeat-preview` to `central_state="idle"`
   - treat that brief `heartbeat_ok` sample as timing noise rather than a
     concrete recovery improvement

No fresh rename drift, firmware drift, upgrade drift, or recovery regression
was observed in this eleventh follow-up capture.
