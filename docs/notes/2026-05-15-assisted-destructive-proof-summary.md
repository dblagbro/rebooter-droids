# Assisted Destructive Proof - .48

- Date: 2026-05-15T12:23:58
- Device: 192.168.1.48
- Firmware: 0.1.22-dev-central-safe

## Recovery Boot
- Triggered from API
- Phone reprovision on setup AP required
- Returned to LAN
- Returned to normal boot with recovery_mode=false
- Key evidence: Recovery provisioning completed; rebooting into normal mode

## Factory Reset
- Triggered from API
- Returned to LAN automatically on dev Wi-Fi
- Came back clean:
  - device_name = Rebooter
  - auth_required = false
  - central_enabled = false
  - central_registered = false
  - recovery_mode = false

## Restore
- Restored prior named bench state
- Auth restored
- Central re-enabled
- Central identity present again after settle window

## Remaining Gap
- Physical button short/3s/10s/30s proof still pending
