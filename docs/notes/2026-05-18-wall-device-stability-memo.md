To: rebooter-droids hub team
From: firmware team
Subject: Wall device stability update after LAN-only recovery pass

We kept the in-wall devices on a LAN-only path and did not require reopening or
serial reflashing them.

Current device-side result:

- `.67` is on `0.1.40-dev-central-safe`, out of recovery, healthy,
  `central_registered=true`, `power=false`
- `.69` is on `0.1.40-dev-central-safe`, healthy,
  `central_registered=true`, `power=false`
- `.30` is on `0.1.40-dev-central-safe`, out of recovery, healthy,
  `central_registered=true`, `power=false`
- `.225` is now also on `0.1.40-dev-central-safe`, healthy, `power=false`,
  but still `central_state=announce_pending` and `central_registered=false`

Short soak result:

- We ran a 10-minute LAN soak across `.67`, `.69`, `.30`, and `.225`
- All four devices stayed reachable the whole time
- No device re-entered `recovery_mode`
- `.67`, `.69`, and `.30` remained registered and progressed through normal
  `idle` / `heartbeat_ok` states
- `.225` remained healthy and monotonic in uptime, but stayed in
  `announce_pending`

Meaning:

- The device-side recovery/stability work is now good enough to keep the
  in-wall units online without taking them back apart
- The remaining `.225` blocker is not another local crash or LAN failure
- `announce_pending` on `.225` means the hub is still telling the device
  "awaiting operator adoption"

Requested hub-side follow-up:

1. Complete the pending adoption / restore flow for `.225`
2. Clean up the stale `.69` hub row whenever convenient, since the live device
   is now healthy again on the newer row

We are intentionally leaving `power=false` on the wall ESP8266 units until the
low-heap power-upload transport is redesigned. The currently validated stable
operating mode is:

- `central=true`
- `power=false`
