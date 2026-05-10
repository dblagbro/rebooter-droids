# Hi firmware team — please bring the 4 lab devices online

Operator has cleaned the central server's device DB to a true zero
state. **Hub is healthy and waiting.** Please complete the action
below — should take ~5 min total.

---

## What I need you to do

For each of these 4 devices, point them at central with the
enrolment token below.

### Per-device

| IP | Display name | Action |
|---|---|---|
| **192.168.1.67** | `test-s31-01` | Already centrally enrolled but stuck in `poll_transport_failed`. **Re-enroll** — clear cached registration, then re-flash / re-config with the URL + token below. |
| **192.168.1.225** | (your call) | Currently `central_management = disabled` by design. **Flip to enabled** + add URL + token. |
| **192.168.1.207** | (your call) | Same as .225 — currently disabled. **Flip to enabled** + add URL + token. |
| **192.168.1.30**  | (your call) | Same as .225 — currently disabled. **Flip to enabled** + add URL + token. |

### The two settings to apply on each device

```
Central management:  enabled
Central base URL:    https://www.voipguru.org/rebooter
Enrolment token:     et_jtpgz47VCfATLQP_qKEC51gpk2_wpFKv
```

### Token specifics

- Single-use **per device** (each device gets its own bearer
  credential after consuming it; you can re-use the same token
  string until it's consumed).
- **30-day TTL — expires 2026-06-09T01:54:54Z UTC.**
- Need more / per-device tokens? Operator can mint via
  `POST /api/v1/admin/enrollment-tokens` (see RFC-002 / API.md).

---

## What the device does on first boot

```
POST https://www.voipguru.org/rebooter/api/v1/device/register
Content-Type: application/json
{
  "enrollment_token": "et_jtpgz47VCfATLQP_qKEC51gpk2_wpFKv",
  "mac_address":      "<device MAC>",
  "hardware_model":   "sonoff_s31",
  "firmware_version": "<your version>",
  "display_name":     "test-s31-01"  // optional, defaults sanely
}

→ 201
{
  "data": {
    "device_id":    "dev_…",
    "device_token": "dt_…",      // long-lived bearer — store on device
    "heartbeat_interval_seconds": 60,
    "poll_interval_seconds":      30
  }
}
```

After that, the device uses `Authorization: Bearer dt_…` for
heartbeat + command-poll.

**v0.4.18 is strict about the register payload** — please make
sure:
- `mac_address` matches `[0-9A-Fa-f:.\-\s]+` (any common format
  works — colons, dashes, dots, mixed-case)
- All string fields are within column widths
  (`display_name` ≤ 120, `hardware_model` ≤ 80,
  `firmware_version` ≤ 40, `mac_address` ≤ 40, `local_ip` ≤ 64,
  `serial_number` ≤ 80, `hardware_revision` ≤ 40)
- Send 400-friendly errors back to your firmware (we return
  `{"error":{"code":"validation_failed","message":"…"}}`)

---

## How to verify it worked (within 60s of the device's first boot)

| Check | Expected |
|---|---|
| `https://www.voipguru.org/rebooter/app/` (operator login) | Verdict flips from **"No devices yet"** to **"All clear · 1 online"** (or similar count) |
| `https://www.voipguru.org/rebooter/app/devices` | Row appears for the device with display_name + heartbeat-state badge |
| `https://www.voipguru.org/rebooter/api/v1/admin/devices` (with admin bearer) | New row in the JSON list |

---

## If it doesn't work — diagnose here

1. **`https://www.voipguru.org/rebooter/app/unregistered-devices`**
   — captures every 401 we see, with source IP + claimed
   device-id + hit count + endpoint. If your device hits us with
   a wrong/expired token, it lands here within seconds. That's
   the first place to check.

2. **Sanity probe the URL itself** from inside the lab subnet
   (we cannot reach the lab subnet from central — different
   network):
   ```
   curl -fsS https://www.voipguru.org/rebooter/api/v1/version
   # expects: {"data":{"version":"0.4.18", …}, "ok":true}
   ```

3. **TLS:** Let's Encrypt cert, no client cert required.

4. **Common pitfalls:**
   - URL must be exact: `https://www.voipguru.org/rebooter`
     (no trailing slash, no `/api/v1` suffix). If the firmware
     compiled-in URL is `https://www2…` or a dev origin, please
     reflash.
   - Token expiry (30 days from 2026-05-09). After that, mint
     a new one.
   - Once a token is consumed by a device, re-using it for a
     SECOND device returns 409 `enrollment_consumed`. Mint a
     fresh token per device if you want to bake one into the
     image.

---

## Hub-side info you might want

- **Health:** `GET /api/v1/version` → `0.4.18`
- **API surface:** `docs/API.md`
- **Endpoint contract:** `/api/v1/device/register`,
  `/api/v1/device/heartbeat`, `/api/v1/device/commands`,
  `/api/v1/device/result`, `/api/v1/device/events`,
  `/api/v1/device/failsafe` (RFC-005)
- **Watchdog rules + schedules** (v0.4.7+) ready to fire once
  devices appear — `failsafe_threshold` /
  `recovery_threshold` / cooldown / etc. all live.
- **Operator's coordination doc** with full endpoint runbook +
  context: `docs/notes/2026-05-09-to-firmware-team-clean-state-and-token.md`

---

## When you're done

Please reply to operator with:

1. Which devices you brought up + their MAC addresses
2. Firmware version flashed
3. Anything you needed to change (URL, token, credentials, etc.)
4. RFC-005 §11 redlines (Q1..Q9 — the safe + fallback firmware
   spec) — outstanding ask, not blocking the bring-up

Hub is at `v0.4.18`, Status page reads "No devices yet" right
now, ready to flip the second one of your devices calls in.
Thanks.

— rebooter-droids hub team
