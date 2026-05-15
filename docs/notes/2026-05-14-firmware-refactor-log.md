# Refactor Log

## 2026-05-14

- scope of refactor:
  - maintainability and remediation pass after QA findings
- key changes:
  - created architecture and design docs
  - tightened public vs protected config behavior
  - added local UI auth/session flow for protected actions
  - improved event-log chronology metadata with `seq`, `boot_id`,
    and `ts_basis`
  - removed unhealthy secondary central default from shipped config validation
  - added API and UI-auth regression scripts
  - fixed a follow-up auth-header merge bug in the browser helper during live retest
- architectural decisions:
  - public local config reads stay available, but central identity/secrets
    are redacted
  - full secret-bearing config export stays on a protected endpoint
  - current-tab session storage is the default local UI auth persistence model
- files impacted:
  - `src/web_server_manager.cpp`
  - `data/index.html`
  - `data/style.css`
  - `data/app.js`
  - `src/config_manager.cpp`
  - `include/types.h`
  - `src/event_log.cpp`
  - `include/event_log.h`
  - `include/firmware_version.h`
- risks:
  - fallback UI and LittleFS UI must stay aligned
  - live-device verification still required for OTA-served assets
- remaining issues:
  - full route-level 405 coverage is still partial
  - button/recovery destructive paths still need hardware-assisted retest
- next recommended actions:
  - finish regression automation
  - validate on `.48`
  - then mirror the artifact and notes to shared locations
