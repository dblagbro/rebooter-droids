# Renamed Test Soak Progress

## 2026-05-20 10:40 ET live recheck

- Rechecked the renamed soak target from the accessible publish checkout because the configured `S:\code\rebooter-droids` workspace is still absent in this session, captured a fresh targeted live probe at `2026-05-20T14:40:58Z`, and refreshed `C:\Users\Administrator\.codex\automations\rebooter-48-upgrade-sweep\latest_probe.json`.
- No new rename drift or hub/device identity drift surfaced in this pass. Authenticated hub Devices UI, hub detail UI, and hub admin devices API still identify `Rebooter - renamed test` at `192.168.1.48` on `0.1.37-dev-central-safe`, and the rendered hub UI still matches the admin API on `offline` / `stale`.
- Concrete live deployment change: hub `https://www.voipguru.org/rebooter/api/v1/version` advanced from the prior verified `0.5.91` sample at `2026-05-20 10:21 ET` to `0.5.102`.
- Concrete reliability issue remains and is stronger by duration: the hub still shows `online=false`, `heartbeat_state="offline"`, `central_status="central_stale"`, the same frozen `last_heartbeat_at="2026-05-20T13:07:15Z"`, and the same stale synthetic latest power sample at `received_at="2026-05-20T13:07:31Z"`, while fresh direct local probes to `/`, `/api/status`, `/api/config`, `/api/system/heartbeat-preview`, and `/api/system/central-diagnostic` all failed with `ConnectTimeout` after about `20.0 s` each. That extends the full local UI/API blackout another ~8 minutes beyond the prior `10:32 ET` capture with no recovery signal.
