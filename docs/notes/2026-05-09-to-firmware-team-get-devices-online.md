# To: Firmware team — getting devices online (none currently checking in)

**From:** rebooter-droids hub team
**Date:** 2026-05-09
**Status / context:** central server is up and on v0.4.0; **zero devices** are
currently checking in. Operator (David) needs the firmware team to confirm
build status / shipping plan / what we still owe you so the first device(s)
land.

This note is the single message the operator can paste verbatim. Three
sections: the **summary** for skim-readers, the **information you need
from us** so you can flash/ship, the **information we need from you** so
the operator can plan rollout.

---

## 1. Summary

- Hub at `https://www.voipguru.org/rebooter/` is healthy on **v0.4.0**.
- Health endpoint: `GET /rebooter/api/v1/version` → `200`,
  `{"version":"0.4.0", ...}`.
- All P1 backend surfaces a device needs are live:
  - **Enrolment / claim** (RFC-005 §enrolment + bootstrap firmware):
    `POST /api/v1/device/claim` and `POST /api/v1/device/heartbeat` on the
    hub. Token-based; QA is exercising both daily.
  - **Failsafe-event ingestion** (v0.3.8): `POST /api/v1/device/failsafe`.
    Events show on the device-detail Failsafe tab and on the Status inbox.
  - **Firmware mirror chain P1** (v0.3.9): per-channel binaries +
    Flask 302-redirect endpoint
    `GET /api/v1/firmware/<channel>/latest` which a freshly-flashed
    device with no token can hit.
- Watchdog rules (v0.4.0) ship the data model + UI + plain-English render
  but the probe runtime (the part that actually power-cycles devices)
  lands in v0.4.1+. Devices don't need to know about that surface yet.

## 2. What you have / can use today (URLs + shapes)

### Channel-pointer (no auth, follows 302):

```
GET https://www.voipguru.org/rebooter/api/v1/firmware/dev/latest
GET https://www.voipguru.org/rebooter/api/v1/firmware/beta/latest
GET https://www.voipguru.org/rebooter/api/v1/firmware/stable/latest
```

Response is `302 Location:` to the actual binary on the same host.
Empty channel returns `404 {"error":{"code":"no_release"}}`.

A bootstrap-only image that serial-flashes once and never has a token
can use this to fetch a main firmware. SHA256 lives on the
`/firmware/<channel>/<filename>.bin` page in the admin console; the
device should verify after download.

### Device API contract (already shipped):

- `POST /api/v1/device/claim` — first-boot, sends MAC + claim token.
  Returns a long-lived bearer.
- `POST /api/v1/device/heartbeat` — `Authorization: Bearer <token>`,
  body `{firmware_version, uptime_s, rssi, ...}`.
- `POST /api/v1/device/failsafe` — same auth, body
  `{failed_version, fallback_to_version, reason, details:{}}`. Use this
  when the safe firmware decides the trial main firmware is wedged.

### Cross-team docs you should already have:

- `docs/RFC-005-safe-and-fallback-firmware.md` — three-slot dual-bank
  ESP8266 architecture (safe-bootstrap + main + fallback) with the
  trial-then-promote state machine. Sent to your team
  2026-05-09 morning. Operator wants your redline on the 9 questions
  marked **Q1..Q9** at the bottom.
- `docs/notes/2026-05-09-to-firmware-team-rca-and-hosting.md` — original
  RCA + hosting summary.

## 3. What the operator needs from you (please reply)

This is the unblock list — please answer each one even if the answer
is "still working on it":

1. **What firmware version is currently being flashed onto the
   bench/lab devices?** None of the Sonoff S31s in the operator's
   inventory are calling our hub, which suggests either (a) no firmware
   has been flashed yet, (b) firmware is flashed but pointed at the
   wrong base URL, or (c) firmware is flashed and the claim-token isn't
   valid. We can't tell from our side.
2. **What base URL is the firmware compiled against?** It must match
   `https://www.voipguru.org/rebooter` exactly (no trailing slash, no
   `/api/v1` suffix). If it's pointing at the older `www2` host or a
   dev origin we'll flip the firmware, not the hub.
3. **Does the firmware have a hard-coded claim-token already, or is
   the operator expected to mint one per device in the admin console
   and bake it in at flash time?** Right now the admin console does
   support per-device pre-claim tokens (see `/app/devices` →
   "Generate claim code"); we just need to know the convention you
   want.
4. **RFC-005 redlines.** Please skim the 9 questions in
   `RFC-005-safe-and-fallback-firmware.md` §11 and respond. The big
   ones: (Q3) trial-window seconds, (Q5) what counts as "main firmware
   healthy enough to promote", (Q7) where fallback is fetched from
   when only safe-bootstrap is alive.
5. **Bootstrap binary delivery.** Are you giving the operator a single
   `.bin` file to flash via esptool, or a `.uf2`/installer flow? If the
   former, what `--baud` and `--flash_size` flags? The operator wants a
   1-page "first device" runbook.
6. **Test device.** The operator has a couple of S31s on a workbench
   that he could flash today if you can hand him a bootstrap image +
   the runbook. He's specifically asked: *"if you need me to pass info
   to the firmware team, let me know what to send them"* — meaning he's
   willing to relay anything you need to debug, including serial logs.

## 4. Info the operator can hand back to you immediately

- **Hub claim endpoint** (CORS open for device origins):
  `POST https://www.voipguru.org/rebooter/api/v1/device/claim`
- **Hub heartbeat endpoint:**
  `POST https://www.voipguru.org/rebooter/api/v1/device/heartbeat`
- **TLS:** valid Let's Encrypt cert, no client cert required.
- **NTP:** the hub clock is correct; if your firmware needs SNTP, any
  public pool works.
- **Logs:** unrecognized-token / 401 attempts are now surfaced on the
  Status inbox (v0.3.6+), so we'll see your devices the moment they
  attempt to talk to us — even if their token is wrong. That gives us
  a fast feedback loop during initial testing.
- **Admin console** (operator login): admin → Devices → "Unregistered
  devices" tab shows every 401 the hub has logged in the last 24 h
  with source IP + claimed-device-id. If your bench device hits us
  with a bad token we'll see it in seconds.

---

If anything in §3 changes after we ship the response, ping the
coordinator-hub channel and we'll update the firmware team's
RFC-005 thread. — rebooter-droids hub team
