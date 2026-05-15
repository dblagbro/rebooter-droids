# To Firmware Team — confirm the power-sample upload keys for estimated current

Date: 2026-05-15
From: rebooter-droids hub/backend team
Re: low-load current semantics (your `2026-05-15-power-capture-and-g2-progress.md`
+ `2026-05-15-pass-to-rebooter-droids-from-firmware.md`)

## What the hub did (v0.5.66 / P1.3)

We absorbed the low-load current clamp you flagged: the CSE7766 firmware
suppresses measured current below ~50 mA, so a real standby load
uploads `i_ma = 0` with a non-zero `p_w`. We now:

- store two new columns on `device_power_samples`: `i_ma_estimated`
  (bool) + `i_ma_estimate` (int mA);
- expose them in the power API + the device-detail card (a clamped
  reading renders as `~20 mA (est)`, not a misleading `0 mA`);
- never treat `i_ma = 0` as "no activity" when `i_ma_estimated` is true.

## The one open question — the upload-row key names

Your notes name the fields as they appear on **`GET /api/status`**:
`power_current_estimated`, `power_estimated_current_ma`. But the
**power-sample upload row** (`POST /api/v1/device/power-samples`) uses
short keys — `i_ma`, `p_w`, `v_v`, `s_va`, … — not the `power_*` status
names.

We don't know which key the firmware actually puts in the *uploaded
sample row*, so the hub currently accepts any of these (first match
wins):

| Hub field | Accepted upload-row keys |
|---|---|
| `i_ma_estimated` | `i_ma_estimated`, `power_current_estimated`, `current_estimated` |
| `i_ma_estimate`  | `i_ma_estimate`, `power_estimated_current_ma`, `estimated_current_ma` |

**Please confirm the exact key names the 0.1.27+ firmware writes into
each uploaded power-sample row.** If they are not in the list above the
hub silently stores NULL — no crash, but the estimate is lost. Once you
confirm, we will pin the hub to the real names and drop the guesswork.

Preferred (for consistency with the existing short upload keys):
`i_ma_estimated` + `i_ma_estimate`.

## Not blocking you

This is informational + one confirmation. The hub side is shipped and
backward-compatible — pre-0.1.27 firmware (no estimated fields) behaves
exactly as before.
