# Night Save State — 2026-05-15

## Firmware line

- Current candidate: `0.1.29-dev-central-safe`
- Artifact:
  - `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.29-dev-central-safe.bin`
- SHA256:
  - `5A1ED2CBB2E6025F48F6DD43DCC00EC812C8F3EE3389A4CBAEFD51B3CFAC1AD2`

## Live fleet state at save time

- `.48` — healthy, online to hub, central stable, still synthetic-only on hub
  because the serial adapter suppresses live CSE7766 reads on that exact unit
- `.67` — healthy, online to hub, live real-power rows flowing
- `.69` — healthy, online to hub, live real-power rows flowing
- `.30` — healthy, online to hub, live real-power rows flowing
- `.225` — still the outlier; remains in `recovery_mode` and offline at the hub

## Important overnight nuance

- `.69` is back on the hub under a **new live row**:
  - `dev_01KRQBRSG1BZ5SR87QQ2KSSVFT`
  - display name currently `lab-69`
- the **old** `.69` row remains present but offline:
  - `dev_01KR9VZKGW72DS7DAQEFAV3T58`
  - display name `Erica's R.L. Speaker`
- this needs a hub-side cleanup/merge decision; do not assume the duplicate is
  already resolved

## Joint verification artifacts

- Fleet/device recovery actions:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\night-sync-device-actions-2026-05-15.json`
- Hub/firmware joint live verification:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\hub-firmware-joint-verification-2026-05-15.json`

## Live captures still running

- 24-hour power capture:
  - PID `47060`
  - output dir:
    - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live`
- G2 timing capture:
  - PID `53464`
  - output:
    - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\g2-time-sync-48-69-live-2026-05-15b.ndjson`

## What was proven tonight

1. The hub at `0.5.68` can still execute:
   - login
   - device register/heartbeat/command-result round-trip
   - power-sample ingest
   - announce → adopt → enrollment-token redelivery → register
2. `.67`, `.69`, and `.30` can now feed live power rows into the hub together.
3. `.225` is now the single residual firmware blocker worth chasing next.

## Resume here

1. Root-cause `.225`.
2. Decide how to reconcile the duplicate `.69` hub identity.
3. Review the overnight power + G2 captures before widening again.
