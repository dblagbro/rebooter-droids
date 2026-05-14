## 2026-05-14 Rebooter-Droids Status Sync And B16 Alignment

Audience:
- Rebooter-Droids team
- product / firmware coordination

Purpose:
- catch up from the last 5+ sprints
- verify which hub-side items already shipped
- identify what is still missing for firmware + B16 power-monitoring alignment

### 1. What has already shipped in the last 5+ sprints

Recent tagged progress in the repo shows the team is materially ahead
of the older canonical backlog docs.

Key ships:
- `v0.5.12` `307530a`
  - B23 + B24
  - B16 ingestion slice
  - deployment completion / status-truth work
- `v0.5.13` `2e9bb93`
  - firmware-scan content-change detection
  - real ICMP ping
- `v0.5.14` `94428c0`
  - inline on/off toggle on devices list
- `v0.5.15` `27cf256`
  - devices + watchdog runtime refactor
- `v0.5.16` `335dac2`
  - Sync tab content refresh
- `v0.5.17` `58a3700`
  - Roku external-source integration
- `v0.5.18` `62cbb61`
  - watchdog helper cleanup
- `v0.5.19` `dcfd5ee`
  - Rules UX phase: edit flow, chips, event details, filterable target picker
- `v0.5.20` `7a5cb70`
  - long-poll `/device/commands` with `Prefer: wait`
- `v0.5.21` `065ff5e`
  - cleanup of underscore compatibility aliases
- `v0.5.22` `ebec921`
  - B21 desired-config blob
  - drift detection
  - push-on-restore
- `v0.5.23` `c4b27d5`
  - B17 adjacent integrations
  - Home Assistant + Weather + iCal

### 2. What we needed that is already done

These hub-side pieces are now clearly present in repo reality:

- **B16 ingestion first slice is shipped**
  - `POST /api/v1/device/power-samples`
  - model exists:
    - `app/models/power_analytics.py`
  - ingest path exists:
    - `app/blueprints/device_api.py`
    - `app/services/events.py`
  - QA exists:
    - `tests/qa/test_device_api.py`

- **Rules edit/display improvements are shipped**
  - `v0.5.19`
  - files:
    - `templates/rules/index.html`
    - `templates/rules/edit.html`
    - `app/blueprints/admin/rules.py`

- **Desired-config / drift work is shipped**
  - `v0.5.22`
  - files:
    - `app/services/device_config.py`
    - `templates/device_detail.html`
    - `app/blueprints/admin/devices_ui.py`

- **Push-on-restore is shipped**
  - this matters directly for firmware recovery / reflash workflows

- **Long-poll device commands are shipped**
  - `v0.5.20`
  - directly relevant to better device responsiveness

- **External-source groundwork is shipped**
  - Roku first, then Home Assistant + Weather + iCal
  - this lines up with the broader multi-modal direction around B16/B17

### 3. What is still clearly missing for our current needs

The repo has **B16 ingestion**, but it does **not** yet have the real
power-monitoring UI/analytics surfaces we now need.

What appears missing:

- no fleet `/app/power` page
- no actual device-detail power-telemetry UI
  - note: `templates/device_detail.html` has a `#power` section today,
    but it is a **power-control** section, not a **power-monitoring**
    section
- no live last-sample power card
- no raw sample table / recent samples view for operators
- no 24h / 7d / 30d power charting
- no rollup UI for daily/hourly aggregates
- no operator-visible distinction between:
  - power control
  - power telemetry
  - power analytics

Also still missing from the hub side:

- explicit site/home profile model for claims-support workflows
- claim-assist/export workflow for spoilage / ISP / utility use cases
- clear UI treatment for centrally disabled devices like `.69`
  - local device is healthy and upgraded
  - but central is disabled, so stale/offline presentation can still be misleading

### 4. What is stale in docs/backlog relative to repo reality

The canonical docs need a cleanup pass because they now understate
what has shipped and overstate some old gates.

Most important stale spots:

- `docs/BACKLOG.md`
  - still says last updated `2026-05-09 PM`
  - does not reflect the real `v0.5.12` through `v0.5.23` state

- `docs/B16-power-analytics-design.md`
  - still says:
    - **Draft**
    - planning-only
    - do not implement until firmware-team replies
  - that was true earlier, but now we have:
    - firmware-side transport work
    - safe fallback progress
    - rollout to `0.1.18-dev-central-safe`
    - live auto-rebind proof
    - known config/report fields

- `docs/redesign-continuation-plan-v2.md`
  - Tier F still says Sonoff S31 has an **HLW8032** chip
  - firmware-side and B16 notes have since corrected this to **CSE7766**

- `docs/PROJECT-STATE-2026-05-09-FULL-SYNC.md`
  - historically valuable, but now far behind current hub + firmware state

### 5. Current firmware-side reality the hub team should align to

Firmware side is now materially farther along than the older hub docs imply.

Current reality:
- main safe firmware:
  - `0.1.18-dev-central-safe`
- safe fallback / recovery is materially improved
- protected config backup / restore exists device-side
- live hub auto-rebind has been proven on the real bench unit
- current firmware version now shows in the device's local web UI
- protected backups were taken on the important reachable devices
- stable and bootstrap `latest.bin` pointers have now both been promoted on `S:`

### 6. Precise asks for the Rebooter-Droids team

These are the hub-side items we need clearly tracked and either
implemented or explicitly backlogged:

1. **Power UI slice**
- add a real power-monitoring surface separate from power-control
- minimum acceptable first slice:
  - device-detail **Power telemetry** card
  - latest sample fields
  - recent-samples table
  - fleet `/app/power` page or equivalent list view

2. **B16 docs/backlog refresh**
- update `docs/BACKLOG.md`
- update `docs/B16-power-analytics-design.md`
- update Tier F references to reflect:
  - CSE7766, not HLW8032
  - B16 ingestion already shipped
  - firmware transport / recovery / rollout status has advanced

3. **Central-status truth for disabled devices**
- explicitly distinguish:
  - central disabled
  - transport stale
  - offline
  - never heartbeated
- `.69` is the concrete example: healthy locally, upgraded, but central disabled

4. **Desired-config scope clarification**
- `device_config.py` already accepts pass-through top-level keys
  including `power`
- but the docs still say only `device_name` is truly exercised end to end
- team should track which keys are now considered supported in practice
  versus merely accepted by schema

5. **Release catalog version-length bug**
- the old long dev filename
  `rebooter-0.1.17-dev-central-safefallback-2026-05-14.bin`
  surfaced a version-length issue during firmware scan
- not a blocker today, but should be cleaned up as hub debt

### 7. Bottom line

The team has made real progress. The main gap is no longer "is the
hub moving?" It is that the **repo's canonical docs/backlog have not
kept pace with the shipped work**, and the **B16 power-monitoring UI
story is still missing even though ingestion and transport are now real**.

That is the next alignment point.
