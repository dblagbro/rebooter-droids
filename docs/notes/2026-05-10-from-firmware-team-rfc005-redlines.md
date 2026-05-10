# From: rebooter firmware team — RFC-005 §9 redlines (CLOSED)

**Received:** 2026-05-10
**Status:** **B12 CLOSED — all 9 questions answered.** Firmware team
also confirmed the announce-poll loop is implemented and live-
validated end-to-end (build `0.1.5-dev-central`, cadence matches our
proposed defaults, no contract deviations).

---

## Summary of decisions

| Q | Topic | Outcome |
|---|---|---|
| Q1 | Slot sizes | A=640 KiB min, B=1 MiB, C=1 MiB. Won't shrink. |
| Q2 | `force` flag | Skip multi-boot trial **but** still require 1 sane boot + heartbeat + uptime sanity. Not "instantly stable". |
| Q3 | Boot-streak N | N=3. Success = normal runtime, Wi-Fi up (or AP intentional), local stack serving, central HB OK (when enabled) OR local-OK (when disabled), uptime ≥ 5 min. Command-poll **not** required. |
| Q4 | Failure threshold F | F=3. Failure = any reset before Q3 success criteria, excluding operator-initiated. Canonical reasons: `watchdog_reset`, `exception_reset`, `boot_loop_timeout`, `manual_reset`, `ota_reboot`, `power_loss_or_external_reset`. No fake brownout detection. |
| Q5 | AP-mode captive portal | **DO IT** — already added to `bootstrap-0.2.2`. Treated as core recovery, not optional. |
| Q6 | Flash-time config | Both. Serial injection (fast path, lab/batch). AP-mode provisioning (universal fallback, field). With announce-flow, central enrollment no longer needed at flash time. |
| Q7 | NVS key layout | LittleFS JSON, not native NVS. Renamed: `boot_slot` (not `boot_target`), `fallback_version` (not `slot_c_version`). Added: `central_enabled`, `setup_ap_name`, `last_boot_reason`, `last_known_good_version`, `pending_enrollment_received_at`. `central_urls` = **JSON array** (not comma-separated). |
| Q8 | Flash-tool packaging | Cross-platform Python CLI **first**, esptool as pip dep. PowerShell script retained as Windows-side reference/recovery. Bundled binary later. |
| Q9 | Hosting timeline | **YES, in force.** Devices using `/rebooter/firmware/` URLs live. New constraint: publish-integrity discipline. Verify post-copy disk SHA **+** external GET body-length on **both** www and www2 before calling a release "live". |

---

## Hub-side follow-ups

These are work items on the rebooter-droids team's side that come
out of the redlines. Not blocking; queued.

### Validation / documentation

1. **Canonical failsafe reason strings (Q4).** The
   `POST /api/v1/device/failsafe` endpoint already accepts an
   arbitrary `reason` string + `details` blob. Update
   `app/services/failsafe.py` (or its blueprint) to recognise the
   6 canonical strings and render them with friendly labels in
   the Status inbox + audit log. Unknown reason values continue
   to pass through verbatim.
2. **Hub Settings → Network tab callout (Q9).** The publish-
   integrity discipline (post-disk-copy SHA + external GET
   body-length on both nodes before calling release live) belongs
   in the Firmware page operator documentation. The on-disk scan
   we shipped in v0.4.19 already records SHA-256 at scan time;
   we should add a "verify external" button that does a `GET`
   against www + www2, compares Content-Length to the expected
   `size_bytes`, and flashes red if anything's off.

### Phased follow-up (not urgent)

3. Update RFC-005 §9 → mark each Q with the firmware team's
   redline + final decision so the doc isn't stale.
4. Add **Q3 success criteria** + **Q4 canonical reason strings**
   as constants in a shared `app/services/failsafe.py` module
   so the operator UI renders them consistently. Today the
   firmware sends free-text and the hub displays it verbatim.

---

## Reply checklist (firmware-team-supplied — for the record)

- Firmware build with announce-poll: **`0.1.5-dev-central`**
- Cadence used: 30s pending, 60s awaiting_register, 1h rejected,
  5xx/transport backoff capped at 60s (matches our proposed
  defaults)
- Response-shape deviation: **none intentional**
- RFC-005 §9 redlines: **all 9 answered**

Bookmark for future deployments: any unit getting stuck during
bring-up should first have its bootstrap version checked. As of
2026-05-10, `bootstrap-0.2.2` includes Wi-Fi AP fallback and is
the recommended baseline.
