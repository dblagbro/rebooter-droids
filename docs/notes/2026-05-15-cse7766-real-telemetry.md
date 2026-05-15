# Real CSE7766 Telemetry

Date: 2026-05-15

## Scope

Replace synthetic bench-only power telemetry with real CSE7766 readings on the
Sonoff S31 firmware path while preserving the existing upload pipeline.

## What changed

- added a dedicated `PowerMonitor` module
- parse live CSE7766 UART frames on `GPIO3`
- expose live power fields through `/api/status`
- emit real `steady` power samples when fresh readings are available
- keep synthetic upload fallback in place if no fresh real sample is available
- delay power-monitor startup until the device is stably up on the LAN

## Live bench result on `.48`

- firmware: `0.1.25-dev-central-safe`
- device: `Rebooter - renamed test`
- power source: `steady`
- measured line voltage: about `119.5V`
- no downstream load attached:
  - `current_ma = 0`
  - `power_w = 0`
  - `apparent_power_va = 0`
  - `power_factor = 1`

## Artifact

- firmware: `C:\dev\rebooter-firmware\.pio\build\sonoff_s31\firmware.bin`
- SHA256: `9A7002C008A3C0CAC393AA01D0E41BA63B38FA9609411FC33DE7885F8A3CF943`
- shared copy:
  - `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.25-dev-central-safe.bin`

## Notes

- ad hoc `curl -F update=@...` uploads produced false-positive OTA acceptance
  signals during this work; `HttpClient` multipart upload was reliable and is
  now the preferred QA path
- next useful validation is a real loaded-power test so current and watts move
  off zero and can be compared against operator expectations
