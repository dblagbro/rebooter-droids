## 2026-05-14 Backlog Items 4 And 5 Progress

### Item 4: bootstrap release decision

Decision made:
- promote both `stable/latest.bin` and `bootstrap/latest.bin`

Why:
- fleet rollout of `0.1.18-dev-central-safe` is complete enough to justify making it the default stable main image
- the safer bootstrap `0.2.5-dev-safe` only makes sense as `latest.bin` if the main stable `latest.bin` it downloads is also current

Actions taken:
- backed up old stable latest pointer to:
  - `S:\code\rebooter-droids\data\firmware\stable\latest.pre-0.1.18-safe-2026-05-14.bak`
- backed up old bootstrap latest pointer to:
  - `S:\code\rebooter-droids\data\firmware\bootstrap\latest.pre-0.1.18-safe-2026-05-14.bak`
- promoted:
  - `S:\code\rebooter-droids\data\firmware\stable\latest.bin` -> `rebooter-0.1.18-dev-central-safe.bin`
  - `S:\code\rebooter-droids\data\firmware\bootstrap\latest.bin` -> `rebooter-bootstrap-0.2.5-dev-safe.bin`

Hashes after promotion:
- stable latest:
  - `1CC861EAAB311945A4FEE42F2792084C94328C6FEAE76CB1CC335380E2872241`
- bootstrap latest:
  - `9A9D1833C602857E5FD35A48F8168F478581E320F3FDB82484294820867545AA`

### Item 5: protected config backups on important reachable devices

Common local admin credentials applied where needed:
- username: `admin`
- password: `BenchPass123!`

Devices backed up successfully:
- `192.168.1.48`
- `192.168.1.30`
- `192.168.1.67`
- `192.168.1.207`
- `192.168.1.225`
- `192.168.1.69`

Behavior observed:
- `.48` already had local auth provisioned and backed up cleanly with the known password
- `.30`, `.67`, `.207`, `.225`, and `.69` returned `409 set admin password before exporting protected backup`, then accepted the new password and exported successfully
- `.69` exported successfully but still has no central identity because the device itself has `central_enabled = false`

Artifacts:
- local:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\protected-backups-2026-05-14\summary.json`
- mirrored:
  - `S:\code\rebooter-droids\docs\notes\protected-backups-2026-05-14\summary.json`

Each device backup is stored in both folders as:
- `<ip-label>-protected-config-backup.json`
