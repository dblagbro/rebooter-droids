# Live hub vs device audit - 2026-05-14

Scope: live `https://www.voipguru.org/rebooter` hub vs reachable
device local APIs. This note is for sprint prep and operator truth,
not yet a cross-team push.

Time of capture:
- Hub version endpoint: `v0.5.11`
- Device list snapshot: about `2026-05-14T03:04Z`

Method:
1. Auth to the live hub using the existing QA/admin path already
   present in `tests/qa/conftest.py`.
2. Fetch `/api/v1/admin/devices` and `/app/devices`.
3. For each device with a `local_ip`, probe `http://<ip>/api/status`
   and `http://<ip>/api/config` from this workstation.

## Fleet summary at capture time

Hub fleet size:
- `7 devices in fleet`
- `Pending adoption: 0`

Hub says online:
- `192.168.1.48`
- `192.168.18.185`
- `192.168.1.67`
- `192.168.1.30`
- `192.168.1.207`

Hub says offline:
- `192.168.1.69`
- `192.168.1.225`

## Concrete findings

### 1. `.225` is locally alive but hub marks it offline

Hub row:
- display name: `Erica''s F.R Speaker`
- local IP: `192.168.1.225`
- hub online flag: `false`
- last heartbeat at hub: `2026-05-14T02:56:37Z`

Local device truth:
- `http://192.168.1.225/api/status` returns `200`
- `wifi_connected: true`
- `central_enabled: true`
- `central_registered: true`
- `central_device_id` matches the hub row
- `central_state: "firmware_check_transport_failed"`
- `central_heartbeat_age_seconds: 509`

Interpretation:
- this is not a dead device
- this is a central transport / heartbeat-path problem
- the hub UI currently reads as "offline device" even though the
  device is reachable locally and still centrally configured

### 2. `.69` is genuinely missing from this workstation''s view

Hub row:
- display name: `Erica''s R.L. Speaker`
- local IP: `192.168.1.69`
- hub online flag: `false`
- last heartbeat at hub: `2026-05-13T22:06:13Z`

Local device truth:
- `http://192.168.1.69/api/status` times out
- `http://192.168.1.69/api/config` times out

Interpretation:
- from this workstation''s perspective, `.69` is genuinely offline or
  unreachable on the LAN
- unlike `.225`, this is not just a hub-state mismatch

### 3. Name-sync drift is still real outside the restore path

Known-good:
- `.48` is correct end-to-end after the manual fix:
  - hub: `Rebooter - renamed test`
  - local API: `Rebooter - renamed test`

Still wrong:
- `.30`
  - hub: `Erica''s Subwoofer`
  - local API: `Rebooter`
- `.225`
  - hub: `Erica''s F.R Speaker`
  - local API: `Rebooter`
- `.207`
  - hub: `Erica''s R.R. Speaker`
  - local API: `Erica''s ?.?. Speaker`

Interpretation:
- `v0.5.8` restore-after-reflash rename push exists
- ordinary fleet rename / desired-name reconciliation is still not
  solved globally
- `.48` proves the device side can accept the name when the hub
  actually pushes it

### 4. `.185` remains hub-online but is not directly reachable from this workstation

Hub row:
- display name: `Devin''s TMR test rebooter`
- local IP: `192.168.18.185`
- hub online flag: `true`

Local device truth from this workstation:
- `http://192.168.18.185/api/status` timed out
- `http://192.168.18.185/api/config` timed out

Interpretation:
- expected cross-LAN limitation from this workstation
- not evidence of a hub bug by itself

## Practical sprint conclusions

1. The "offline" bucket currently mixes at least two meanings:
   - truly unreachable (`.69`)
   - reachable locally but central transport stale (`.225`)

2. Desired-name drift remains a live product bug, not historical
   paperwork.

3. The Devices page is useful, but it still hides the distinction
   between:
   - LAN reachable
   - central stale
   - central disabled
   - truly dead

## Suggested next sprint checks

1. Add a device-detail / devices-list distinction between:
   - `offline`
   - `central transport failed`
   - `locally reachable but hub stale`

2. Finish ordinary desired-name push so it is not restore-only.

3. Add one admin-side diagnostic action:
   - "ping local UI from hub/helper" or "last local reachability result"

4. Re-test `.225` after the next central transport fix:
   - success condition is the hub row flipping back to online without
     any local reflash or manual re-adopt.
