To: Rebooter-Droids Team / Next Codex
From: Firmware Team
Date: 2026-05-19
Subject: READ THIS FIRST - authoritative firmware handoff dossier

If you are resuming the firmware/device side of this project from scratch, the
authoritative handoff is the cold-start dossier in this repo:

- `docs/notes/2026-05-19-cold-start-dossier-from-firmware.md`
  (firmware-workstation copy:
  `C:\dev\rebooter-droids-publish\docs\notes\2026-05-19-cold-start-dossier-from-firmware.md`)

Do not rely on older shorter memos as your primary source. The dossier above is
the full continuity packet and includes:

- current live device state
- a retroactive 2026-05-15 through 2026-05-19 communication-gap audit that
  backfills updates that were not actually delivered live at the time
- operator/physical constraints
- repo paths and git SHAs
- current firmware artifact hash
- known working local auth
- what was tried
- what worked
- what failed
- what is still blocked
- the `www` / `public_base_url` routing analysis
- the Wi-Fi fallback and resiliency requests
- exact commands and artifact paths
- what is safe to do next
- what not to do next

Critical detail to preserve exactly:

- `www.voipguru.org` is not just a URL style - it is a required hostname.
- A missing `www` can be a routing bug.
- The firmware and hub defaults are sane (both point at
  `https://www.voipguru.org/rebooter`), but a runtime override on
  `network.public_base_url` or `REBOOTER_PUBLIC_BASE_URL` could still cause the
  hub to emit the wrong host to adopted devices.

When auditing the live hub, check exactly:

- `network.public_base_url`
- `REBOOTER_PUBLIC_BASE_URL`
- any DB override on `settings.public_base_url`
- what `central_register_url` is actually emitted to newly adopted devices

This bears directly on the `192.168.18.185` same-LAN routing concern called
out in the dossier.

Supporting artifacts referenced by the dossier:

These artifacts live on the firmware workstation, not in this repo:

- live status snapshot:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-19-live-status-snapshot.json`
- `.225` short watch:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\watch-225-short-2026-05-19.ndjson`
- `.67` / `.69` / `.30` short comparison watch:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\watch-67-69-30-short-2026-05-19.ndjson`
- wall soak:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\wall-soak-2026-05-18-short.ndjson`
- OTA results:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\ota-67-to-0.1.40-2026-05-18.json`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\ota-30-to-0.1.40-2026-05-18.json`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\ota-225-to-0.1.40-2026-05-18.json`

Built firmware artifact at handoff:

- Version: `0.1.40-dev-central-safe`
- Path: `C:\dev\rebooter-firmware\.pio\build\sonoff_s31\firmware.bin`
- SHA256: `EB7E6CB1688675DC3FE031640A0C1448D071C82B0B72C0B76169DA5A48B5E8BF`
- Firmware repo HEAD at handoff: `fd3dfc3`
- Hub repo HEAD at handoff: `e0db940`

Most important practical takeaway before you do anything risky:

- the wall devices should currently be treated as:
  - `central=true`
  - `power=false`
- and even that safer profile is not fully stable yet, because the dossier's
  late watch corrections show `.225`, `.69`, and `.30` still rebooting.

