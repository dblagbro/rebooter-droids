# To: Firmware team — clean fleet state + ready-to-use enrolment token

**From:** rebooter-droids hub team
**Date:** 2026-05-09 PM
**Status:** Hub on **v0.4.14**, devices DB **fully reset**, **fresh
30-day enrolment token waiting**. The next move is firmware-side.

---

## TL;DR

1. **Hub is clean.** Every device row, enrollment token, and
   unregistered-attempt has been wiped — Status page reads
   "No devices yet" because there genuinely are zero devices.
2. **One enrolment token is waiting** with a 30-day TTL. Use it
   to bring the lab devices online. **Token below.**
3. **Hub URL:** `https://www.voipguru.org/rebooter`
4. **Endpoints, schema, and runbook** below.

---

## 1. The token

Single-use per device. Re-mint as many as you need.

```
Token (raw, capture-now-or-regenerate):
  et_jtpgz47VCfATLQP_qKEC51gpk2_wpFKv

Token id (admin-side reference):  et_01KR7SGE8M88X83EGYEJGN8YJV
Expires (UTC):                    2026-06-09T01:54:54Z
Display-name hint:                physical-lab-device
Note:                             "for firmware team to flash + bring real lab devices online"
```

If the firmware team flashes the bootstrap + main image with this
token baked into the device-side config, the first-boot enrolment
flow will swap it for a long-lived bearer credential.

If the team needs **per-device** tokens (one token per
serial-flash so you can identify which board consumed it), mint
extras via the admin API:

```bash
TOKEN=<bootstrap-admin-bearer-jwt>
curl -X POST https://www.voipguru.org/rebooter/api/v1/admin/enrollment-tokens \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name_hint": "office-modem-A",
    "note": "device-S31-serial-XYZ",
    "ttl_seconds": 2592000
  }'
```

`ttl_seconds` is now honoured (BUG-043 fix in v0.4.14). Cap is
30 days.

---

## 2. Device-side flow the hub expects

### 2.1 First boot — exchange enrolment token for bearer

```
POST https://www.voipguru.org/rebooter/api/v1/device/register
Content-Type: application/json
{
  "enrollment_token": "et_…",
  "mac_address":      "DC:4F:22:AB:CD:EF",
  "hardware_model":   "sonoff_s31",
  "firmware_version": "x.y.z",
  "display_name":     "office-modem-A"   // optional
}
```

Response:
```
HTTP 201
{
  "data": {
    "device": { "id": "dev_…", "display_name": "office-modem-A", … },
    "device_token": "dt_…"   // store this. Long-lived bearer.
  }
}
```

The `device_token` is the credential for every subsequent call.

### 2.2 Heartbeat (recommended every 60-300s)

```
POST https://www.voipguru.org/rebooter/api/v1/device/heartbeat
Authorization: Bearer dt_…
{
  "firmware_version": "x.y.z",
  "uptime_s":         12345,
  "rssi":             -42,        // optional
  "free_heap":        18432,      // optional
  "local_ip":         "192.168.1.67"  // optional but operator-friendly
}
```

### 2.3 Polling for commands (every 5-30s recommended)

```
GET https://www.voipguru.org/rebooter/api/v1/device/commands
Authorization: Bearer dt_…
```

Returns 0..N pending command rows; mark each delivered + execute
+ POST result back.

### 2.4 Failsafe event (RFC-005 dual-bank rollback)

If the safe-bootstrap rolled back from a failed main-firmware
update:

```
POST https://www.voipguru.org/rebooter/api/v1/device/failsafe
Authorization: Bearer dt_…
{
  "failed_version":      "x.y.z",
  "fallback_to_version": "x.y.w",
  "reason":              "watchdog_timeout",
  "details":             { "boot_count": 3, "last_log": "…" }
}
```

Surfaces on the Status inbox as a critical attention item.

---

## 3. What we cleaned up + why

The DB had **130 QA-fixture devices + 1522 enrollment tokens + 66
unregistered-auth-attempt rows** from heavy QA testing (test
suite ran ~30+ end-to-end cycles in the past 24 h). All
properly tagged `is_qa_fixture=true` or `qa-` prefixed, but the
volume was making the Devices list confusing.

We took the opportunity to reset to zero and start fresh:

| Table | Before | After |
|---|---|---|
| devices | 130 | **0** |
| enrollment_tokens | 1522 | **1** (the one above) |
| unregistered_auth_attempts | 66 | **0** |
| commands / heartbeats / events | (large) | **0** |
| groups / sites | 108 | **0** |

So when the firmware team's first device hits `/device/register`,
it'll be the only row in the table.

---

## 4. What we need from the firmware team

Per the prior comm doc (`docs/notes/2026-05-09-to-firmware-team-get-devices-online.md`),
we still need answers on:

1. **What firmware version is currently flashed on the lab
   devices** (192.168.1.67 / .225 / .207 / .30)?
2. **What base URL is the firmware compiled against?**
   Must match `https://www.voipguru.org/rebooter` exactly. If
   it's pointing at `www2` or a dev origin, please reflash.
3. **RFC-005 §11 questions Q1..Q9** — the safe + fallback firmware
   spec. We want your redline.
4. **Bootstrap-binary delivery format** — single .bin via
   esptool? Flash-time tool? Operator-friendly?

---

## 5. How to verify it worked (operator side)

Within ~60 seconds of the firmware team's first device booting
with the token:

- **Status page** at `https://www.voipguru.org/rebooter/app/`
  flips from "No devices yet" to "All clear N online".
- **Devices list** at `/app/devices` shows the new row with
  display_name + heartbeat-state badge.
- **Audit log** at `/app/audit` shows
  `device.registered_via_enrollment_token` action.

If the device hits the hub but the token is wrong, the
**Unregistered-Devices page** at `/app/unregistered-devices`
captures the 401 attempt with source IP + claimed-device-id +
hit count — that's our diagnostic surface.

---

## 6. Hub posture

- Hub: rebooter-droids v0.4.14 on tmrwww01.
- Health: `https://www.voipguru.org/rebooter/api/v1/version`
- Watchdog runtime: live, ticks every 10 s.
- Schedule runtime: live, ticks every 30 s.
- Email (invites, password reset, watchdog notifications): SMTP
  configured but EarthLink credential needs operator update —
  emails to recipients *outside* dblagbro@earthlink.net's
  Vade-protected list will reject (see BUG-030 / RecipientsRefused
  notes in bug-log). Doesn't affect device flow.
- Test suite: 292 passing (post-v0.4.13). New e2e wall-clock
  watchdog tests added in v0.4.14.
