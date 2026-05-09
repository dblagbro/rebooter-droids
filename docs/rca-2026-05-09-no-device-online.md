# RCA — "no device ever shows online" (2026-05-09, post-v0.3.4)

| Field | Value |
|---|---|
| Reported by | operator |
| Symptom | "I have not yet seen a device show as online" across the entire v0.3.x session |
| Investigation | direct LAN probe + container log search + DB inspection + synthetic end-to-end smoke |
| Result | server-side healthy; cause is operational + device-side |
| Code defect found in passing | bulk-delete dual-checkbox bug (fixed in v0.3.5) |

## 1. Server-side: healthy

Verified via two independent paths.

**v027 heartbeat-state QA suite — 7/7 green:**
- `test_device_serializer_includes_heartbeat_state`
- `test_just_enrolled_device_reports_never`
- `test_device_with_recent_heartbeat_reports_online`
- `test_dashboard_stats_break_out_never_heartbeated`
- `test_devices_list_renders_never_badge_distinctly`
- `test_devices_list_renders_online_badge_distinctly`
- `test_device_detail_never_renders_friendly_hint`

**Synthetic register → heartbeat → check round-trip via the public API:**

```
POST /api/v1/admin/enrollment-tokens   → 201 (token minted)
POST /api/v1/device/register           → 201 (device + bearer token)
POST /api/v1/device/heartbeat          → 200 (heartbeat accepted)
GET  /api/v1/admin/devices/<id>        → last_heartbeat_at populated
GET  /api/v1/admin/devices?…           → heartbeat_state == "online"
DELETE /api/v1/admin/devices/<id>      → cleanup
```

Round-trip ran cleanly against the live deployment immediately
after the RCA was started. The handler at
`app/services/heartbeats.py::record_heartbeat()` correctly
inserts a `DeviceHeartbeat` row AND updates
`devices.last_heartbeat_at` in the same transaction.

## 2. Why no real device shows online

### 2.1 Container access logs

Last 3 hours of `docker logs rebooter-droids` filtered to
`/api/v1/device/*`:

- **Every POST is from `192.168.18.1`** — that's the docker
  bridge gateway, used by QA tests running in this same host.
- **Zero POSTs from any LAN IP** in the 192.168.1.x range.
- **Zero 4xx/5xx responses on the device API** — the real
  devices aren't even attempting to call.

### 2.2 LAN probe of the four known lab IPs

| IP | Per project state | Ping (RCA window) | HTTP :80 |
|---|---|---|---|
| 192.168.1.67 | `test-s31-01` — central-enrolled, last known heartbeat-failing | **100% packet loss** | n/a |
| 192.168.1.225 | local-only | **100% packet loss** | n/a |
| 192.168.1.207 | local-only | OK (ttl=62) | TCP-RST / connect refused |
| 192.168.1.30 | local-only | **100% packet loss** | n/a |

Three of four devices are completely off the network. The fourth
pings but its HTTP service is dead. nmap of the entire
192.168.1.0/24 subnet finds no Sonoff-shaped HTTP responses —
the only IIS/web servers in the .200-range are unrelated
Windows hosts (e.g. 192.168.1.215 = IIS).

### 2.3 Configuration history

Per the canonical pause-state document at
`docs/PROJECT-STATE-2026-05-09-FULL-SYNC.md` §2:

- 192.168.1.67 (`test-s31-01`) — central enabled, central
  registered, but central state failing with heartbeat/poll
  transport failure.
- 192.168.1.225 / .207 / .30 — `central management = disabled`
  **by design**. They are local-only devices and will never
  call this server.

So **3 of 4 devices are not configured to heartbeat to central
in the first place** — they would never have shown "online" on
the central UI even when they were healthy on the LAN. The
operator's "never seen a device show online" observation is
expected for those three.

### 2.4 The one centrally-enrolled device (.67)

`test-s31-01` was the only device that should have been calling
this server. It's now unreachable on the LAN entirely — 100%
packet loss. The pause-state document already flagged the
heartbeat/poll transport as failing; the device may have crashed
its firmware-side network loop, lost Wi-Fi, or been powered off.

### 2.5 The bulk-delete cascade (separate v0.3.4 bug)

While investigating, found that `device_heartbeats` table is
empty (count = 0). This is consistent with the v0.3.4
bulk-delete bug having wiped every device row, which cascade-
deleted every heartbeat row via the
`ondelete="CASCADE"` FK on `device_heartbeats.device_id`. So
the bulk-delete bug is a separate code defect, addressed in
v0.3.5.

## 3. Conclusion

The "no device shows online" observation has **two distinct
causes operating simultaneously**:

1. **Three of four lab devices are local-only by design.**
   They are not bugs and will never appear in central's
   "online" count without a configuration change on the device
   side (`central_management = enabled` + valid enrollment
   token).
2. **The fourth device (the centrally-enrolled one) is offline
   on the network.** Power, Wi-Fi, or firmware crash. Cannot be
   diagnosed further without physical access.

The central server's heartbeat path is verified-healthy.
**No code change is required on the server side to make this
problem go away.** The operator's hint that "we may need new
firmware for them too" is consistent with the device-side state.

## 4. What was fixed in code anyway

While running this investigation, the bulk-delete dual-checkbox
bug was discovered and fixed in v0.3.5:

- `static/js/bulk_select.js` — pair-sync checkboxes by
  `name + value` so the master-uncheck/operator-uncheck flow
  doesn't leave a hidden pair checked.
- All four bulk handlers dedupe incoming id lists as
  defense-in-depth.

## 5. What needs to happen operationally to actually see a device online

1. **Power and Wi-Fi:** confirm 192.168.1.67 (and the other
   three at their current DHCP-leased IPs) are powered on and
   joined to Wi-Fi. ARP / nmap scan was already done; the
   devices simply aren't on the LAN.
2. **Firmware health:** if the device is powered and on Wi-Fi
   but its HTTP/heartbeat loop isn't running, that's a firmware
   bug in the sibling repo `https://github.com/dblagbro/rebooter-firmware`
   (not this repo).
3. **Central enrollment:** the three local-only devices need
   `central_management_enabled = true` AND a fresh enrollment
   token registered if the operator wants them to appear in the
   central UI.
4. **Token re-issue after the bulk-delete:** since v0.3.4's
   bulk-delete wiped the device records, any device that had a
   bearer token from before will get 401 on its next call. The
   firmware's "401 → re-enroll" path (per pause-state §4) should
   self-recover, but it needs a fresh enrollment token to do
   that — the operator must mint one in the central UI and
   point the device at it (or the firmware must auto-discover,
   which it currently doesn't).

## 6. Possible diagnostic improvements (queued, not v0.3.5)

- Surface 401-rejected device-auth attempts on the Status
  inbox (R-DSH-3) as an attention item: "Device X tried to call
  but its token was rejected — re-enrol it." The data already
  lives in `unregistered_auth_attempts`; just needs a service
  function and an attention-feed entry.
- Add an outbound LAN-side reachability probe so the operator
  can see at a glance which device IPs respond to ping from
  the central host. Optional; only useful if central + lab
  share an L3 boundary.
- Document in the firmware-side README that an `enrollment_token`
  field can be reset via the device's local web UI in case of
  401 — so the operator has a recovery path without re-flashing.
