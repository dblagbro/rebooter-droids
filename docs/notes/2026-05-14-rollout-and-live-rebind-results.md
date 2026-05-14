## 2026-05-14 Rollout And Live Rebind Results

Scope completed:
- backlog item 1: roll out `0.1.18-dev-central-safe`
- backlog item 2: live-verify hub auto-rebind on the real running hub

### Firmware rollout

Primary artifact used:
- `S:\code\rebooter-droids\data\firmware\stable\rebooter-0.1.18-dev-central-safe.bin`

Devices upgraded directly over local OTA:
- `192.168.1.67`
- `192.168.1.30`
- `192.168.1.207`
- `192.168.1.225`
- `192.168.1.69`

Device upgraded through hub deployment:
- `192.168.18.185`

Already on target before this pass:
- `192.168.1.48`

Current result:
- `.48` on `0.1.18-dev-central-safe`
- `.185` on `0.1.18-dev-central-safe`
- `.67` on `0.1.18-dev-central-safe`
- `.30` on `0.1.18-dev-central-safe`
- `.207` on `0.1.18-dev-central-safe`
- `.225` on `0.1.18-dev-central-safe`
- `.69` locally upgraded to `0.1.18-dev-central-safe`, but central is disabled on-device, so hub inventory may still look stale/offline there

Artifacts:
- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\fleet-rollout-2026-05-14\rollout-summary.json`
- `S:\code\rebooter-droids\docs\notes\fleet-rollout-2026-05-14\rollout-summary.json`

### Live hub auto-rebind verification

Bench device used:
- `http://192.168.1.48/`

Test performed:
1. Took protected config backup
2. Cleared local `central.enrollment_token`
3. Cleared local `central.device_id`
4. Cleared local `central.device_token`
5. Rebooted device
6. Observed live rebind behavior against the real hub

Observed result:
- device returned on Wi-Fi
- device re-registered successfully
- original `device_id` was preserved
- device settled at `central_registered = true`
- device settled at `central_state = idle`

Backup artifacts:
- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-rebooter-48-live-rebind-backup.json`
- `S:\code\rebooter-droids\docs\notes\2026-05-14-rebooter-48-live-rebind-backup.json`

### Important caveat

During hub firmware scan, the older long dev filename
`rebooter-0.1.17-dev-central-safefallback-2026-05-14.bin`
surfaced a version-length problem for the release catalog path.
This did not block the `0.1.18-dev-central-safe` rollout, but it
should be cleaned up later by keeping release version strings under
the current DB length limit.
