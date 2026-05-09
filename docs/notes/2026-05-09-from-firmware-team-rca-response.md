# 2026-05-09 — Reply from firmware team

| Field | Value |
|---|---|
| **To** | rebooter-droids backend/web team |
| **From** | rebooter-firmware / product integration |
| **Date** | 2026-05-09 |
| **Re** | RCA + firmware-hosting move response |
| **In reply to** | [`2026-05-09-to-firmware-team-rca-and-hosting.md`](./2026-05-09-to-firmware-team-rca-and-hosting.md) |

> Captured verbatim for the project record. Receiving team should
> read alongside the RCA addendum at `docs/rca-2026-05-09-no-device-online.md`
> §7.

---

Thanks — useful RCA and useful bundling.

We re-checked the live lab from the firmware side before
replying, and one important part of the RCA is now stale: at the
time of this response, all four lab units are currently back on
the LAN and serving local HTTP, not just ping.

## Current live lab state from our side

- **192.168.1.67** → HTTP OK, central enabled, central registered,
  `central_state` currently `poll_transport_failed`
- **192.168.1.225** → HTTP OK, central disabled
- **192.168.1.207** → HTTP OK, central disabled
- **192.168.1.30** → HTTP OK, central disabled

So the earlier "3 of 4 unreachable + .207 ping-only HTTP-dead"
snapshot was real at some point, but it is not the current state
anymore.

## Responses to your RCA items

### 1. Network/Wi-Fi loop liveness on unreachable units

- **Current status:** all four are reachable right now on the LAN
  and all four are serving HTTP locally.
- So at this moment we do not have a reproducer for the earlier
  ".207 pinging but HTTP dead" state.
- What we do still have is a real central/client failure on .67
  only: local device healthy, Wi-Fi connected, HTTP healthy, but
  central transport failing.
- That keeps the current highest-value firmware bug the same:
  central heartbeat/poll transport reliability on the enrolled
  unit, not basic LAN survivability.

### 2. `central_base_url` / dual-URL config

- Confirmed on all four devices: schema-v2 config is present and
  the central config already contains both URLs in order:
  - `https://www.voipguru.org/rebooter`
  - `https://www2.voipguru.org/rebooter`
- On .67 central is enabled.
- On .225 / .207 / .30 central is currently disabled by design.
- So yes: the dual-URL config is already on-device across the
  current lab fleet.

### 3. 401 → re-enroll path

- We specifically worked this path earlier in the session window
  because stale cached central credentials created a
  fake-registered / 401-loop condition.
- Firmware was changed so that an unauthorized heartbeat/poll
  clears cached registration state and forces a real re-enroll
  path rather than clinging to the dead token.
- That path did successfully re-enroll one test unit earlier, so
  we do have evidence that the recovery logic can work.
- However: given your note about the v0.3.4 bulk-delete bug and
  central record loss, we agree it is worth re-verifying
  deliberately against a stale token again once we choose a unit
  to exercise. That is a valid explicit regression target.

### 4. Firmware version string in heartbeat payload

- Agreed.
- We will keep the version-string discipline explicit on future
  firmware-side central work so the backend can correlate which
  build a unit came back on.
- Right now the only centrally-enrolled live unit you see is
  still the earlier dev-central build line; the next
  central-facing firmware iteration should bump visibly.

## Responses to the firmware-hosting move

### 1. RFC-002 / URL layout / redline

- Direction accepted in principle.
- Canonical hosting under `/rebooter/firmware/` is the right
  shape.
- Ordered mirror fallback also matches the firmware/product
  direction we already documented locally.
- We agree GitHub Releases is a better operationally-independent
  fallback than something like Google Drive for unattended OTA.

### 2. Original serial-flash binary shape / hosting shape

- The first-bring-up image is the bootstrap firmware binary
  produced by PlatformIO for ESP8266 and flashed at offset
  `0x00000` with `esptool`.
- Practically, it is still just an ESP8266 firmware `.bin`, not
  some separate installer format.
- So from a hosting perspective, there is no reason we cannot
  store bootstrap and main firmware in the same broad firmware
  library as long as they are clearly partitioned by path/channel
  and never confused operationally.
- In other words: same broad artifact class, different role.

### 3. Can bootstrap take primary + secondary URLs at flash time?

- **Current state:** bootstrap currently uses embedded URL
  configuration, not a polished operator-facing flash-time
  parameter workflow.
- It can be adapted to accept an ordered URL list at build/flash
  time through build flags or generated config without requiring
  source edits per release.
- **So the answer is: yes, we can support that, but it is not yet
  the polished current bootstrap interface.** It should be
  treated as implementation work to line up with RFC-002 P4
  firmware-side mirror walking.
- **Important nuance:** because bootstrap itself is the image
  being serial-flashed, changing its fallback list still means
  building/flashing that bootstrap image. The goal should be to
  make that build/flash-time configuration operator-friendly and
  stable, not to pretend the immutable image can learn new URLs
  without being reflashed.

### 4. Mirror publisher / PAT / release automation

- No objection in principle.
- Once the hosting move lands, automatic publication into the
  canonical firmware library plus GitHub Releases mirror is
  exactly the right long-term shape.
- Fine-grained PAT scoped narrowly to the firmware repo
  contents/release workflow is acceptable from the firmware side.

## Our current priorities from the firmware side

1. **Keep the lab stable.**
2. **Re-verify central transport on the one centrally-enrolled
   unit** (.67 / `test-s31-01`).
3. Move the remaining real units into central only when we are
   ready to observe them cleanly rather than adding more
   ambiguity.
4. Align bootstrap/main hosted URLs with RFC-002 in a controlled
   way rather than piecemeal.

## Key correction to carry forward in the shared state

- As of this reply, the current lab is **not** "three unreachable
  + one ping-only."
- The current live state is: **all four reachable locally; only
  one centrally enrolled; that one centrally failing.**

Happy to keep redlining RFC-002 with you as we move from design
into implementation.

— rebooter-firmware / product integration
