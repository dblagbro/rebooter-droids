# Fleet bring-up — current shared state (2026-05-10 04:17 UTC)

Captures the result of the firmware-team bring-up run. **3/4 lab
devices online; 1 device-specific blocker.**

---

## Online and idle

3 devices centrally registered, heartbeating cleanly, sending
30s command-polls. All on firmware build `0.1.0-dev-central`.

| Local IP | MAC | device_id | Display name | Last heartbeat (UTC) |
|---|---|---|---|---|
| 192.168.1.225 | `C4:D8:D5:0C:F6:B3` | `dev_01KR812687CEGS7CHXJQ7QAW4H` | `lab-225` | 04:16:58 |
| 192.168.1.207 | `C4:D8:D5:0C:F7:59` | `dev_01KR8126MTTZW22E8F2QVFWT68` | `lab-207` | 04:16:58 |
| 192.168.1.30  | `C4:D8:D5:0C:F7:A5` | `dev_01KR8127W5XMP6MDF34J0TXQP9` | `lab-30`  | 04:16:59 |

Lab egress NAT IP (as seen by hub): `47.230.251.21`.

## Remaining blocker — `lab-67`

| Field | Value |
|---|---|
| Local IP | `192.168.1.67` |
| MAC | `c4-d8-d5-0c-f7-ca` |
| Alias | `lab-67` |
| Device-side state | `central_enabled=true, wifi_connected=true, relay_on=true, central_registered=false, central_state=register_transport_failed` |

### Actions attempted (firmware side, all between 03:50 and 04:15 UTC)

1. Fresh per-device enrolment token applied
2. Stale cached registration (device_id/token) cleared via config change
3. OTA-updated to current `0.1.0-dev-central` main firmware
4. Retry with dual URLs (`www.voipguru.org/rebooter` + `www2…/rebooter`)
5. Retry forced to secondary-only `https://www2.voipguru.org/rebooter`

### Hub-side observation across all attempts (verified by direct nginx + DB inspection)

- **0** `POST /api/v1/device/register` from `47.230.251.21` (or any IP) for a 4th device
- **0** rows in `unregistered_auth_attempts` (which captures every 401 with claimed_device_id + IP)
- **0** 4xx responses on `/api/v1/device/*`
- **0** 5xx responses
- **0** Python exceptions / tracebacks in container logs
- **0** log lines matching MAC `c4:d8:d5:0c:f7:ca` (case-insensitive)

The hub **never sees a packet** from this device. www and www2 routes both verified `200 v0.4.18` from the public internet. Failure is upstream of the network from the hub's perspective — the request never leaves the device side coherently.

## Diagnosis next steps (firmware side)

The hub-side is clean. The next signal will come from `.67`'s
serial / boot console, which would show the actual transport-layer
error backing the firmware's `register_transport_failed` flag (one of:
DNS resolution, TCP connect, TLS handshake, HTTP request timeout,
clock-related cert validation). Hardware-fault on this specific unit
is also possible — the 3 working units share the same firmware build
+ same lab egress + same MAC vendor block, ruling out config and
network-path classes.

## Hub posture (snapshot)

- Version: **v0.4.18**
- Test suite: 302 passing, 6 expected skips
- Open code-fix bugs: 0
- Database: 3 real devices, 0 QA fixtures, 0 unregistered_auth_attempts
- Enrolment tokens still valid: 1 (the fleet token, expires 2026-06-09;
  per-device tokens minted by firmware team for the bring-up are
  individual)
- Watchdog runtime: live (10s tick)
- Schedule runtime: live (30s tick)

## Communication trail

- `docs/notes/2026-05-09-to-firmware-team-clean-state-and-token.md` — clean-state handoff with endpoint runbook
- `docs/notes/2026-05-09-to-firmware-team-bring-devices-online-NOW.md` — per-device action note (fueled this bring-up)
- This file — final state record
