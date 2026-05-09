# QA Notes

Operational notes, environment quirks, and known limitations encountered
while testing.

## Test environment

- Tests run against the live deployment at
  `https://www.voipguru.org/rebooter`. There is no separate staging
  environment as of v0.1.2.
- Bootstrap admin: `dblagbro@gmail.com` / value of
  `REBOOTER_BOOTSTRAP_ADMIN_PASSWORD` in `/home/dblagbro/docker/.env`.
- Test data is created with the prefix `qa-` (groups, sites) or
  `QA <thing>` (display names) so cleanup queries can find it.

## Known quirks

- nginx is bind-mounted on a single config file. Editor atomic-rename
  changes the inode and `nginx -s reload` won't see the new content
  until the container is restarted. Use `sudo docker restart nginx`
  after editing the conf.
- Postgres data directory uses a sub-folder (`PGDATA=…/cluster`) because
  the parent bind mount is non-empty (gitkeep). This is intentional.
- The APScheduler instance is single-worker, gated by Postgres advisory
  lock 4242117310. Only one Gunicorn worker runs `expire_commands`.

## Test-data cleanup

Currently manual. After a regression run, devices/groups/sites tagged
with `qa-` should be removed via:

```sql
DELETE FROM commands WHERE issued_by_user_id IS NOT NULL
  AND device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM device_events WHERE device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM deployment_assignments WHERE device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM device_credentials WHERE device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM enrollment_tokens WHERE consumed_by_device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM devices WHERE display_name LIKE 'QA %';
DELETE FROM groups WHERE name LIKE 'qa-%';
DELETE FROM sites WHERE name LIKE 'qa-%';
DELETE FROM firmware_releases WHERE filename LIKE 'rebooter-qa-%';
```

The suite attempts cleanup on success — manual fallback above.

## Run history

### 2026-05-08 — first deep-regression pass (v0.1.2 → v0.1.3)

- 77 tests written; 62 passed first run, 3 hardening probes failed.
- Three real bugs found and shipped in v0.1.3:
  - BUG-001: enrollment-token race (high)
  - BUG-002: firmware concurrent-upload 500 (high)
  - BUG-003: trailing-slash 404 (medium)
- Six hardening findings logged (BUG-005..011); see `bug-log.md` and
  `remediation-plan.md`.
- One operator-reported issue (group-create logout, BUG-004) could not
  be reproduced in clean Playwright session; left in `monitoring`.

### How to re-run

```bash
cd /mnt/s/code/rebooter-droids
python3 -m pytest tests/qa -v
# or just the hardening probes:
python3 -m pytest tests/qa/test_hardening_probes.py -v
```

The suite hits the live deployment by default. Override with
`REBOOTER_QA_BASE=https://.../rebooter` for staging.
