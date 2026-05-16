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
