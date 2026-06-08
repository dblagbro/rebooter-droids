# UI feature flags (post-brutal-review redesign)

The 10-PR brutal-review redesign shipped between 0.6.24 and 0.6.31.
Each surface is independently revertible via an environment variable so a
regression at 02:00 doesn't require a hub rollback — flip the affected
flag to `0`, `docker compose up -d --force-recreate --no-deps rebooter-droids`,
and the old code path returns.

All flags default to ON in `app/config.py`. Set `=0` (or `false`/`no`/`off`)
to revert.

| Env var | Default | Shipped | What it gates |
|---|---|---|---|
| `REBOOTER_UI_HERO_V2` | ON | 0.6.24 | Devices-list hero sentence ("All N devices are healthy" / "N devices need attention: …") + recent-reboots line. Reverting restores the 5-chip strip. |
| `REBOOTER_UI_ROW_V2` | ON | 0.6.25 | Per-row badge collapse. QA / 🔒 / registration_state demoted to a muted meta line; `State` column dropped from the header. Reverting restores the 4-7-pill row. |
| `REBOOTER_UI_TABLE_V2` | ON | 0.6.26 | Single responsive table (CSS-reflowed cards <640px). Reverting brings back the duplicate `.v3-device-cards` mobile rendering. |
| `REBOOTER_UI_A11Y_V2` | ON | 0.6.24 | Skip-link as first focusable element; `<main id="main" tabindex="-1">` landmark. Reverting hides the skip-link (the focus-ring CSS stays). |
| `REBOOTER_UI_DETAIL_V2` | ON | 0.6.27 | Device-detail primary-action hero (one state sentence + one button above the tab strip) + sticky tab strip. Reverting drops back to the 8-anchor scroll. |
| `REBOOTER_UI_NAV_V2` | ON | 0.6.29 | Nav trim — Status and Power removed from both top and bottom bars (still reachable via brand link / ⌘K / direct URL). Reverting restores the 6-item bars. |
| *(none)* | — | 0.6.28 | ⌘K command palette. No flag — it's additive and works regardless. Disable by deleting `static/js/cmdk.js` if needed. |
| *(none)* | — | 0.6.30 | Mobile swipe gestures + sticky hero on `(hover:none)` viewports. No flag — gated on touch-only via media query. Disable by deleting `static/js/swipe.js`. |
| *(none)* | — | 0.6.31 | Optimistic relay flip + 4s snap-back-on-timeout. No flag — it reuses the existing SSE infrastructure and degrades to "instant flip without snap-back" if the SSE bus is down. To force the pre-0.6.31 behaviour, comment out the optimistic block in `static/js/devices_live.js`. |
| `REBOOTER_UI_COPY_V2` | ON | 0.6.24 | Reserved — text changes ("Enrol"→"Add device", "never heartbeated"→"never reported in") ship unconditionally. Setting `=0` is a no-op; revert via `git revert` if needed. |

## Quick-revert recipe

If a redesign surface is breaking your specific workflow:

```bash
# Pick the offending flag (e.g. hero replaced something you relied on)
echo 'REBOOTER_UI_HERO_V2=0' | sudo tee -a /opt/C1/rebooter-droids/.env
sudo docker compose up -d --force-recreate --no-deps rebooter-droids
```

Reverts take effect on next render — no DB migration, no template
recompile, no devices-side change.

## Hotfix history (post-ship issues)

- **0.6.32** — `⌘K` overlay was painting on every page load with the
  scrim covering the UI; Escape didn't close it because the JS state
  said "not open." CSS rule `.v3-cmdk-overlay[hidden] { display: none
  !important }` restored the `hidden` attribute's behaviour; JS now
  also closes on Escape regardless of internal state.
- **0.6.33** — `/api/v1/admin/search` (feeder for ⌘K) was 500-ing
  because the `_pages()` list used `admin_ui.firmware_page` (a typo —
  the real endpoint is `admin_ui.list_firmware_page`). One-line fix.

Both caught by `~/ui-audit/audit.py` — see `audit.py` and run it after
any UI-touching ship.
