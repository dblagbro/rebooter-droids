# UI audit — Playwright sweep for the post-brutal-review redesign

`tools/ui-audit.py` is a runnable Playwright script that catches the
class of regressions the brutal-review redesign introduced — and that
manual testing missed twice already (the 0.6.32 cmdk-open-on-load bug
and the 0.6.33 `/api/v1/admin/search` 500). Run it after any
UI-touching ship.

## What it checks

- All 7 admin pages load with status 200 at both desktop (1440×900)
  and mobile (375×812) widths.
- The ⌘K command palette is **hidden** on initial page load (the
  0.6.32 regression).
- Ctrl+K opens the palette; Escape closes it (the 0.6.32 lock-out).
- The skip-link is the first tab target (PR-6 a11y).
- The brand link points at `/app/` (post-PR-9 nav trim).
- `/api/v1/admin/search` returns 200 with at least one match (the
  0.6.33 regression).
- The device-detail primary-action hero renders (PR-7).
- Zero JavaScript console errors or warnings across all pages.

Failures + screenshots are written to `tools/ui-audit-out/` (gitignored).

## Run it

The script needs an authed session. Since the hub stores the admin
password as a bcrypt hash (and you should never paste your password
into a script), mint a Flask signed session cookie out-of-band via the
container:

```bash
# 1. Mint a session cookie for your admin user.
sudo docker exec rebooter-droids python3 -c "
from app import create_app
from flask.sessions import SecureCookieSessionInterface
from app.db import session_scope
from sqlalchemy import select
from app.models import User
app = create_app()
with app.app_context():
  with session_scope() as s:
    u = s.execute(select(User).where(User.email == 'YOUR-EMAIL')).scalar_one()
  ser = SecureCookieSessionInterface().get_signing_serializer(app)
  print('COOKIE', ser.dumps({'user_id': u.id}))
" > tools/ui-audit-cookie.txt

# 2. Run the audit.
python3 tools/ui-audit.py
```

Output ends with a one-line pass/fail summary:

```
=== behaviour summary: 7 / 7 passed ===
=== console errors/warnings: 0 ===
```

## Known limitations

- **Not yet in GitHub Actions.** The CI gate runs an ephemeral instance
  without a sessions table populated for any user, so the cookie-mint
  step has nothing to mint against. A follow-up could seed a known
  admin during CI fixture setup and run the audit against the
  ephemeral instance. Tracked in the backlog.
- **Mobile pass uses only viewport emulation.** Touch swipes, sticky
  positioning under iOS Safari URL-bar dynamics, and PWA install
  prompts aren't covered. Manual mobile QA remains the source of
  truth for those.
- **Auth-only pages.** Public surfaces (`/app/login`, signup) aren't
  audited because the cookie loads us straight into the admin shell.
  Add a separate `--no-auth` pass if a public-surface regression is
  ever a concern.

## Re-baselining

If a deliberate redesign change breaks an assertion, update the script
in the same commit. The audit is a regression net, not a spec — the
spec is the brutal-review synthesis in the conversation history.
