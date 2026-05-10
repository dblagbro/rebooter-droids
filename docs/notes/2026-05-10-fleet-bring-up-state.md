# Fleet bring-up — RESOLVED (2026-05-10 04:35 UTC)

Captures the result of the firmware-team bring-up run.
**4/4 lab devices online and registered.** Original blocker
on `lab-67` resolved by reducing on-device BearSSL buffer
sizes; root cause was TLS-side, not network or hub.

Original status during the 04:17 UTC checkpoint snapshot was
3/4 with `lab-67` failing on `HTTPC_ERROR_CONNECTION_FAILED -1`.
Final-state record below.

---

## Online and registered (final state, 04:35 UTC)

All 4 devices centrally registered; all `registration_state=active`.

| Local IP | MAC | device_id | Display name | Firmware | Last heartbeat (UTC) |
|---|---|---|---|---|---|
| 192.168.1.225 | `C4:D8:D5:0C:F6:B3` | `dev_01KR812687CEGS7CHXJQ7QAW4H` | `lab-225` | `0.1.0-dev-central` | 04:34:42 |
| 192.168.1.207 | `C4:D8:D5:0C:F7:59` | `dev_01KR8126MTTZW22E8F2QVFWT68` | `lab-207` | `0.1.0-dev-central` | 04:34:37 |
| 192.168.1.30  | `C4:D8:D5:0C:F7:A5` | `dev_01KR8127W5XMP6MDF34J0TXQP9` | `lab-30`  | `0.1.0-dev-central` | 04:27:37 (transient stale ~7 min at snapshot) |
| 192.168.1.67  | `C4:D8:D5:0C:F7:CA` | `dev_01KR82K0W2WTA2968QEDG0Y42K` | `lab-67`  | `0.1.1-dev-central` | 04:34:38 |

Lab egress NAT IPs seen by the hub: `47.230.251.21` (the original
3 units), and `192.168.1.11` for `lab-67`'s register call (the
fix-attempt traffic apparently routed through a different lab
vantage / debug shell during the secondary-URL retry).

`lab-67`'s register hit at 04:33:36 UTC, immediately followed by
heartbeats — no errors recorded.

## Resolved blocker — `lab-67`

**Root cause: per-unit BearSSL buffer sizing on the ESP8266 TLS
stack.** The default secure-client buffer allocation on the .67
unit was failing the TCP-connect-into-TLS-handshake transition
silently, surfacing as `HTTPC_ERROR_CONNECTION_FAILED -1` from
ESP8266HTTPClient before any HTTP request line was emitted —
which matched our hub-side observation of zero packets across
all monitor windows for the unit's MAC.

**Fix:** firmware OTA to `0.1.1-dev-central` *plus* a reduction
in BearSSL secure-client buffer sizes on the device.

**Verification (post-fix, 04:33:36 UTC):**
- DNS resolution: ok
- `tcp_connect_ok = true`
- `https_code = 200` for `GET /api/v1/version` (where it
  previously returned `-1`)
- `POST /api/v1/device/register` succeeded with HTTP 201;
  device id minted (`dev_01KR82K0W2WTA2968QEDG0Y42K`)
- Heartbeats flowing within seconds; `state=online` confirmed
  on the admin Devices page

| Field | Value |
|---|---|
| Local IP | `192.168.1.67` |
| MAC | `c4-d8-d5-0c-f7-ca` |
| Alias | `lab-67` |
| Final firmware | `0.1.1-dev-central` (with reduced BearSSL buffers) |
| Final state | online, registered, heartbeating |

### Actions attempted (firmware side, 03:50 → ~04:33 UTC)

1. Fresh per-device enrolment token applied
2. Stale cached registration (device_id/token) cleared via config change
3. OTA-updated to current `0.1.0-dev-central` main firmware
4. Retry with dual URLs (`www.voipguru.org/rebooter` + `www2…/rebooter`)
5. Retry forced to secondary-only `https://www2.voipguru.org/rebooter`
6. OTA-updated to `0.1.1-dev-central` adding transport-stage diagnostics
7. Hard power-cycle via upstream relay
8. **(fix)** Reduced BearSSL secure-client buffer sizes on the
   device → register succeeded immediately on the next attempt
   via `https://www2.voipguru.org/rebooter`

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

### Latest device-side diagnostic (post 0.1.1-dev-central + power-cycle)

Firmware reports the failure as **`HTTP code -1: connection failed`
before any request reaches the hub.** In `ESP8266HTTPClient` this
maps to `HTTPC_ERROR_CONNECTION_FAILED` — the underlying TCP connect
to the central host's :443 never completed. So:

- The HTTP layer never gets to send a request line
- Hence no log entry on the hub (consistent with our 0/0/0/0/0
  observation across every monitor window)
- Wi-Fi is up and local HTTP works, so it's not a layer-1/2/IP issue
- The 3 sibling units on the same Wi-Fi + same lab egress + same
  firmware build connect cleanly to the same host:443

**Plausible device-side causes (in order of likelihood):**

1. **Per-unit TLS/certificate trust store issue** — wrong CA bundle
   flashed, expired CA pin, or storage corruption on the cert blob.
   Sibling units flashed from the same build don't show this, which
   points to a unit-specific persistent-storage / NV-section problem.
2. **Wi-Fi-to-WAN egress NAT entry stuck** — some home routers
   keep a stale conntrack entry pinned to a previous (now-dead)
   socket; subsequent SYNs to the same destination get dropped.
   Power-cycling the device alone won't clear this; the upstream
   router needs a connection-table flush. This is consistent with
   the 3 sibling units (which presumably opened their first
   connection while .67 was already failing) succeeding, while .67
   keeps hitting the dead entry.
3. **Hardware fault on .67's RF / TCP path** — radio retransmit
   storm, bad antenna, etc. that doesn't surface on local HTTP
   (LAN traffic) but kills the longer-distance WAN handshake.
4. **Device clock so far off that TLS rejects upfront** — but that
   would normally show as a different error code in
   ESP8266HTTPClient, not -1.

### Recommended next experiment

Have the device, as a single one-shot synthetic from its serial /
debug shell:

```
1. ping www.voipguru.org           — confirms DNS + ICMP routes
2. ping 47.230.251.21 (or whatever
   www.voipguru.org resolves to)   — confirms IP reachable
3. tcp-connect to that IP:443      — distinguishes DNS-OK-but-
                                     TCP-fails from earlier-stage
4. tls-handshake to that IP:443    — distinguishes connect-OK-
                                     but-TLS-fails
```

That sequence pinpoints which stage is failing and proves whether
the issue is shared-with-network or specific to the unit's TLS
stack. The hub will see the connect attempt land or fail at each
of those stages.

If steps 1-3 all succeed but the firmware's HTTP client still
returns -1 with no hub-side log entry, that's a strong indicator
of a per-unit TLS-stack problem (mode 1 above).

**Outcome — diagnosis was correct (TLS-stack, not network or
hardware).** The ESP8266 BearSSL secure-client allocator was the
real issue. Hypothesis #1 ("per-unit TLS trust store / cert blob
issue") was the right family but the wrong specific cause —
not a corrupted CA bundle, but a sizing constraint on the TLS
working buffers. Worth bookmarking for future ESP8266 deployments:
**any unit that gets `HTTPC_ERROR_CONNECTION_FAILED -1` with
DNS ok and `tcp_connect_ok=true` should try a buffer-size reduction
on the BearSSL secure client before assuming a hardware / cert
fault.**

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
