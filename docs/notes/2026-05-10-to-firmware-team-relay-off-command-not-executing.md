# To firmware team — relay_off (and other relay_*) commands from hub command queue do not flip the relay

**Date:** 2026-05-10 (evening)
**Affected fleet:** Erica's living-room speaker set (5 devices on
firmware versions `0.1.2-dev-central` / `0.1.3-dev-central` /
`0.1.5-dev-central`).
**Hub version:** rebooter-droids v0.4.29 (live on
`https://www.voipguru.org/rebooter`).

## Symptom (operator-reported)

Operator clicked the `relay_off` button on the hub WebUI's device
detail page. The relay did NOT turn off. Operator then opened the
device's own local WebUI (`http://192.168.1.67/`) and toggled the
relay there — it turned off immediately. So:

- **Hub-issued command path: broken** (relay does not flip)
- **Device-local WebUI path: working** (relay flips)

This rules out the relay hardware, the device-side power supply,
and anything physical. The bug is purely in how the device firmware
acts on commands fetched from the hub's `/api/v1/device/commands`
endpoint.

## Hub-side evidence — hub did its job

We re-ran the path on 2026-05-10 22:38 UTC against
`dev_01KR82K0W2WTA2968QEDG0Y42K` ("Erica's F.L Speaker", firmware
`0.1.3-dev-central`, IP `192.168.1.67`). Postgres rows:

```
commands table:
  id           = cmd_01KRA0NAX00XRFX3ZGTCZJQGWC
  type         = relay_off
  status       = expired (after 10 min TTL)
  created_at   = 2026-05-10 22:38:24+00   ← hub enqueued
  delivered_at = 2026-05-10 22:38:26+00   ← device polled & got it (+2s)
  completed_at = NULL                     ← device never reported back
```

`command_results` table for that command_id: **0 rows**.

`device_heartbeats` for the same device across the window:

```
  22:38:24+00   relay_on = TRUE
  22:39:24+00   relay_on = TRUE     ← after delivery; should be FALSE
  22:40:24+00   relay_on = TRUE
  22:41:24+00   relay_on = TRUE
  22:42:24+00   relay_on = TRUE
```

So the device:
1. **Successfully polled** the hub and was handed the `relay_off`
   command (we have `delivered_at` populated, and the hub-side
   command-poll handler atomically marks the row `accepted` when
   the device fetches it).
2. **Did not flip the relay** (heartbeats keep reporting
   `relay_on=true`).
3. **Did not call** `POST /api/v1/device/command-result` to report
   either `completed` or `failed`.

The hub then let the command expire after its 10-minute TTL —
correct hub behaviour given no result was ever reported.

This is a clean **device-side execution failure**, not a delivery
or queueing problem.

## Earlier evidence in the same fleet

Same pattern on two earlier commands against this device set —
both `relay_on` and `relay_off`. Pattern: `delivered_at` is set,
`completed_at` is NULL, heartbeat `relay_on` does not change. We
didn't catch this earlier because the operator didn't have the
fleet named yet so the failures looked like flakiness.

## Possible root causes for firmware team

In rough order of likelihood:

1. **Firmware's command dispatcher accepts the command from
   `/device/commands` but never routes it to the relay-control
   function.** The fact that we see `delivered_at` set means the
   firmware's GET poll succeeded and Content-Type/JSON parsed OK.
   But somewhere between "command received from hub" and "relay
   pin driven low/high" the chain breaks. Check:
   - Is there a separate code path for hub-driven vs. local-UI-
     driven relay toggle? If so, only the local-UI path has been
     wired through; the hub path doesn't reach the same toggle
     function.
   - Is the firmware filtering on a `mode` field? We see one device
     in the audit log with a `set_mode` command earlier today; if
     the dispatcher only honours `relay_*` commands when
     `mode=smart_plug` AND that check is broken/missing, we'd see
     this exact symptom. (The fleet's heartbeats all report
     `mode=smart_plug` so the gate ought to be open.)

2. **Firmware executes the relay flip but the result-callback path
   is silently failing.** Less likely because the heartbeats also
   show `relay_on=true` continuously — if the relay flipped and
   only the callback was broken, the heartbeat right after would
   show `relay_on=false` even without the callback.

3. **Firmware silently drops commands it doesn't recognise as a
   string match.** Unlikely because `relay_off` is the canonical
   spelling and the local-UI path presumably uses the same string
   internally, but worth a quick grep.

## What we need from the firmware team

1. **Confirm whether the hub-command path is wired to the same
   relay-toggle function as the local-WebUI path.** If they diverge,
   that's the bug.
2. **A serial-log dump from one of these devices** while we
   re-issue a hub-side `relay_off` would settle it in five
   minutes. Hub can re-trigger on demand — just ask.
3. **A firmware fix targeting the dispatch path**, and a version
   bump (e.g. `0.1.6-dev-central`) so the hub can offer it as a
   real upgrade. (Side note: the hub's upgrade-button logic
   was broken until **v0.4.29 this evening** — it was offering
   downgrades — so even when a fixed firmware is registered, the
   button will only show up if its version number is strictly
   newer than what the fleet runs. The fleet currently has devices
   on `0.1.5-dev-central`, so the fixed build needs to be at least
   `0.1.6-dev-central` to be offered to all of them.)

## Hub-side reproduction recipe (for firmware team or QA)

```bash
# Get a session cookie
curl -sf -c /tmp/c -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"<admin>","password":"<pw>"}' \
  https://www.voipguru.org/rebooter/api/v1/auth/login

# Enqueue a relay_off against the target device
curl -sf -b /tmp/c -X POST \
  -H "Content-Type: application/json" \
  -d '{"type":"relay_off","payload":{},"ttl_seconds":120}' \
  "https://www.voipguru.org/rebooter/api/v1/admin/devices/<device_id>/commands"

# Wait one device-poll cycle (≤ 30s) and read back the command
# state + heartbeats from the hub Postgres:
sudo docker exec rebooter-droids-pg psql -U rebooter -d rebooter -c \
  "SELECT id, type, status, delivered_at, completed_at FROM commands WHERE id='cmd_…';"
```

## Pointer

Live Postgres rows preserved on the hub container as of v0.4.29
deploy at 2026-05-10 22:42 UTC; backups in
`/home/dblagbro/backups/rebooter-droids/rebooter-droids-db-v0.4.28-20260510T223558Z.sql.gz`.
