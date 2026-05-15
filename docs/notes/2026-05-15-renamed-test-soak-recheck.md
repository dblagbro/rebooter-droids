# Rebooter - renamed test soak recheck

Date: 2026-05-15

Scope in this run:
- Rechecked published hub/device contracts and release notes for the renamed-test soak thread.
- Direct local workspace inspection and direct live URL fetches were unavailable in this session, so only repo-backed and publicly published evidence is recorded below.

Concrete reliability issues:

1. `apply_config` schema drift remains between the hub API reference and the device integration guide.
   - `docs/API.md` says the backend accepts top-level keys `device_name`, `relay_restore_behavior`, `monitor_interval_seconds`, `boot_warmup_seconds`, `manual_button_enabled`, `internet`, `device`, `notifications`, and `power`.
   - `docs/DEVICE_INTEGRATION.md` omits `power` from the accepted top-level keys.
   - Risk: a device UI/firmware implementation built from the device guide can silently lag the hub contract for power-related config pushes.

2. Fallback-host guidance is still missing from the device integration guide even though the deployed hub architecture documents a live fallback.
   - `CHANGELOG.md` v0.2.1 says `https://www2.voipguru.org/rebooter/` is live and firmware clients should configure both URLs with primary-first fallback.
   - `docs/DEVICE_INTEGRATION.md` still documents only one `central_base_url` example and one server contract base URL.
   - Risk: recovery behavior can be weaker than the deployed hub supports if device-side central config follows the guide literally.

Improved finding:

1. Recovery/status-truth work is materially less blocked than in earlier passes.
   - `CHANGELOG.md` v0.5.33 records that the firmware-side heartbeat-contract expansion shipped on 2026-05-14 (`0.1.19-dev-central-safe`) and explicitly marks Phase 3 recovery/status absorption on the hub as actionable.
   - This is a real unblocker for the renamed-test soak iteration because recovery-state comparisons no longer depend on a hypothetical future heartbeat contract.

Not asserted in this run:
- No claim about the current live admin UI rendering for the renamed-test device.
- No claim about the current live device-local UI or device-local API behavior.
- No change was made to contact the Rebooter-Droids team.
