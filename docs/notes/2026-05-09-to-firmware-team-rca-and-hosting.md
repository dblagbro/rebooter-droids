# 2026-05-09 — Note to firmware team

| Field | Value |
|---|---|
| **To** | rebooter-firmware team (`https://github.com/dblagbro/rebooter-firmware`) |
| **From** | rebooter-droids backend/web team |
| **Date** | 2026-05-09 |
| **Re** | "no device shows online" RCA + firmware-hosting move |
| **Status** | Draft — operator to redline before sending |

---

Hi firmware,

Two things bundled, since they connect.

## 1. RCA on the "no device shows online" report

Operator flagged this morning that no device has shown `online` on
the central UI across the v0.3.x window. We did a full
investigation (writeup at `docs/rca-2026-05-09-no-device-online.md`
in this repo); summary:

- **Central server is healthy.** Synthetic register → heartbeat
  round-trip flips a device to `online` immediately. v027 + v035
  QA buckets green end-to-end. The heartbeat handler persists to
  both `devices.last_heartbeat_at` and the `device_heartbeats`
  event log.
- **No real-device traffic has hit the server in 24 h+.** Every
  `POST /api/v1/device/*` in the access logs is from
  `192.168.18.1` (the docker bridge gateway used by our own QA).
  Zero requests from any LAN IP.
- **Three of four lab devices are unreachable on the LAN as of
  the RCA window.** 192.168.1.{67, 225, 30} are 100% packet loss;
  .207 pings (ttl=62) but its HTTP service is dead (TCP-RST on
  port 80). Per the project pause-state, three of those four were
  `central_management_enabled=false` by design — local-only — so
  they were never going to call central regardless. The fourth
  (`test-s31-01` / .67) was the only centrally-enrolled unit and
  it's the one currently dead on the network.

**Items we'd like the firmware side to confirm or check:**

1. **Network/Wi-Fi loop liveness on the unreachable units.** If
   they're powered but not on the LAN, that's a firmware-side
   state. The .207 unit pinging but HTTP-dead is the most
   diagnostic — TCP stack is up, the HTTP/heartbeat task isn't.
   Crashed or never-started?
2. **`central_base_url` value on each unit.** With v0.3.3+ the
   central is dual-URL (`www.voipguru.org/rebooter` +
   `www2.voipguru.org/rebooter`). Confirm the firmware tries both
   in order.
3. **401 → re-enroll path.** A side effect of a v0.3.4 bulk-delete
   bug (now fixed in v0.3.5) is that any pre-existing device record
   in the central DB is gone, so any device with a cached bearer
   token will get 401 on its next call. Per the project pause-state
   §4 the firmware is supposed to detect this and re-enroll
   cleanly. Worth re-verifying that path actually works against a
   stale token; this is likely to come up for `test-s31-01` once
   it's back on the air.
4. If you ship a new firmware build, please bump the version
   string in the heartbeat payload so we can see in the central
   logs which units came back on which build — helps close the
   loop on this RCA.

## 2. Firmware-hosting move (operator-directed)

The operator wants firmware hosting consolidated into this
project — including the **original flash file** that we burn over
serial on first bring-up, not just the OTA main firmware.
Specifically:

- **Move bootstrap + main firmware hosting under
  `/rebooter/firmware/`** on this central project. The site that
  has been carrying these so far should retire its copy. RFC-002
  (`docs/RFC-002-firmware-mirrors.md` in this repo) already
  designs the canonical layout — please skim it; we built it
  explicitly to receive this hand-off.
- **Maintain a firmware library** — keep every released version
  retrievable, not just the latest. Per-channel sub-paths
  (`bootstrap/`, `dev/`, `beta/`, `stable/`) per RFC-002 §7.5.
- **Stable "latest" filename slot.** The freshly-serial-flashed
  bootstrap needs a known-stable URL to fetch the latest main
  firmware on first boot. Operator's call:

  > "always updating the latest to the file name the flashed
  > serial devices pull first."

  Concretely, that means the bootstrap firmware should embed
  something like:

  ```
  https://www.voipguru.org/rebooter/firmware/bootstrap/latest.bin
        (the bootstrap library — keeps every version forever, but
        `latest.bin` always points at the freshest bootstrap)
  https://www.voipguru.org/rebooter/firmware/main/stable.bin
        (what the bootstrap pulls down to install the main fw)
  ```

  …and central operations always update `latest.bin` /
  `stable.bin` to point at the actual freshest binary in the
  library. The versioned files (`rebooter-0.x.y.bin`) stay
  forever for archival/rollback; the "channel pointer" files
  always re-point.
- **Mirror chain** — RFC-002 also designs an ordered fallback so
  a device whose primary URL is unreachable falls through to a
  secondary (GitHub Releases as the operationally-independent
  mirror). Bootstrap firmware should support trying URLs in order
  — even if its first call hits a stale primary.

Concretely the action items on your side:

1. **Pull RFC-002, redline it.** Anything in §7.6 (URL layout)
   that the bootstrap firmware can't tolerate, raise now.
2. **Identify what the original serial-flash binary needs**
   (image format, offset, etc.) so we know the right hosting
   shape — if it's the same `.bin` shape as OTA firmware we can
   host them in the same library; if not we'll partition.
3. **Confirm the bootstrap firmware can be told the primary +
   secondary URLs at flash time** so the multi-URL fallback works
   without a re-flash.
4. Once this lands, the central's RFC-002 mirror-publisher (P1 →
   P4 of that RFC) will take over publishing every release into
   the library + GitHub Releases mirror automatically when we
   release. We'll need a fine-grained PAT scoped to
   `contents:write` on the firmware repo for the
   GitHub-Releases publisher.

No urgency on item-2 work in this same window — the operational
priority is getting the lab devices back on the network so we can
see one show `online` end-to-end. Hosting move is the bigger
structural change and can ship after.

Reply via the usual channel; happy to redline RFC-002 in a
sidebar before any backend work starts.

Thanks,
**rebooter-droids backend/web team**
