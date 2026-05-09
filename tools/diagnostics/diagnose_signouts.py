"""Diagnose the frequent-signouts complaint.

Two hypotheses to test in order:
1. Cross-host: cookie set by www.voipguru.org doesn't carry to
   www2.voipguru.org. If the operator switches between the two
   URLs, every switch = re-login.
2. Single-host longevity: cookie expires or is invalidated within
   a single browser session.

If (1) reproduces, that's almost certainly the bug — the cookie
has no `Domain=` attribute today, so it's host-scoped.
"""

from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

EMAIL = os.environ.get("REBOOTER_QA_EMAIL", "dblagbro@gmail.com")
PASSWORD = os.environ.get("REBOOTER_QA_PASS", "Super*120120")

PRIMARY = "https://www.voipguru.org/rebooter"
SECONDARY = "https://www2.voipguru.org/rebooter"


def _login(page, base_url: str) -> bool:
    page.goto(f"{base_url}/app/login")
    page.fill('input[name="email"]', EMAIL)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    return "/app/login" not in page.url


def _is_signed_in(page, base_url: str) -> tuple[bool, str]:
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")
    return ("/app/login" not in page.url, page.url)


def main() -> None:
    print("=== rebooter-droids signout diagnosis ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=os.environ.get(
                "PLAYWRIGHT_CHROMIUM_PATH",
                "/home/dblagbro/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
            ),
            args=["--no-sandbox"],
        )
        ctx = browser.new_context()
        page = ctx.new_page()

        # ── Hypothesis 1: cross-host bleeding ────────────────────────────
        print("\n[H1] Login at PRIMARY, then visit SECONDARY")
        ok = _login(page, PRIMARY)
        print(f"  login at {PRIMARY}: {'OK' if ok else 'FAILED'}")

        cookies = ctx.cookies()
        for c in cookies:
            if c.get("name") == "session":
                print(
                    f"  cookie 'session': domain={c.get('domain')!r} "
                    f"path={c.get('path')!r} secure={c.get('secure')} "
                    f"httpOnly={c.get('httpOnly')} sameSite={c.get('sameSite')!r} "
                    f"expires={c.get('expires')}"
                )

        signed_in_primary, url1 = _is_signed_in(page, PRIMARY)
        print(f"  hit {PRIMARY}/app/: signed_in={signed_in_primary} (url={url1})")

        signed_in_secondary, url2 = _is_signed_in(page, SECONDARY)
        print(f"  hit {SECONDARY}/app/: signed_in={signed_in_secondary} (url={url2})")

        if signed_in_primary and not signed_in_secondary:
            print("  ✦ HYPOTHESIS 1 CONFIRMED: cookie scoped to www, fails on www2")
        elif signed_in_primary and signed_in_secondary:
            print("  ⊖ Cookie carries cross-host (unexpected). H1 NOT confirmed.")
        else:
            print("  ⊘ Login itself failed, can't isolate H1. Recheck creds.")

        # ── Hypothesis 2: single-host longevity ──────────────────────────
        print("\n[H2] Single-host longevity — 5 page hits over ~10 s")
        ctx2 = browser.new_context()
        page2 = ctx2.new_page()
        ok = _login(page2, PRIMARY)
        print(f"  login at {PRIMARY}: {'OK' if ok else 'FAILED'}")
        for i, path in enumerate([
            "/app/",
            "/app/devices",
            "/app/rules",
            "/app/history",
            "/app/settings",
        ], 1):
            page2.goto(f"{PRIMARY}{path}")
            page2.wait_for_load_state("networkidle")
            here = page2.url
            ok = "/app/login" not in here
            print(f"  [{i}] GET {path}: {'OK' if ok else 'BOUNCED to login'} ({here})")

        # ── Hypothesis 3: SECONDARY single-host longevity ───────────────
        print("\n[H3] Same single-host check against SECONDARY")
        ctx3 = browser.new_context()
        page3 = ctx3.new_page()
        ok = _login(page3, SECONDARY)
        print(f"  login at {SECONDARY}: {'OK' if ok else 'FAILED'}")
        for i, path in enumerate([
            "/app/",
            "/app/devices",
            "/app/rules",
            "/app/history",
        ], 1):
            page3.goto(f"{SECONDARY}{path}")
            page3.wait_for_load_state("networkidle")
            here = page3.url
            ok = "/app/login" not in here
            print(f"  [{i}] GET {path}: {'OK' if ok else 'BOUNCED to login'} ({here})")

        browser.close()


if __name__ == "__main__":
    main()
