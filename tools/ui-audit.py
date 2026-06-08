"""Playwright UI audit for rebooter-droids 0.6.32 (post-hotfix).

Verifies the brutal-review redesign at desktop + mobile widths, plus
the specific bugs the operator hit (cmdk-on-load, Esc lock-out). Writes
screenshots, a console-error log, and a JSON summary into ./out/.

Run: python3 audit.py
"""
from __future__ import annotations

import json
import pathlib
import sys
from playwright.sync_api import sync_playwright, Page, BrowserContext, ConsoleMessage


HUB = "https://www.voipguru.org/rebooter"
OUT = pathlib.Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def read_session_cookie() -> str:
    """Read the pre-minted Flask session cookie from disk. The mint
    happens out-of-band via docker exec so this script never touches
    the operator's password."""
    path = pathlib.Path(__file__).parent / "session-cookie.txt"
    for line in path.read_text().splitlines():
        if line.startswith("COOKIE "):
            return line.split(" ", 1)[1].strip()
    raise SystemExit("session-cookie.txt has no COOKIE line — re-run the mint step")


def attach_cookie(ctx: BrowserContext, cookie_value: str) -> None:
    ctx.add_cookies([{
        "name": "rebooter_session",
        "value": cookie_value,
        "domain": "www.voipguru.org",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }])


class ConsoleSink:
    """Collects console messages + page errors per page."""
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def attach(self, page: Page, url: str) -> None:
        # 0.6.33 audit harness fix: prior attach() registered fresh listeners
        # on every call, so a 500 from one navigation surfaced as N console
        # entries (one per prior attach). page._already_attached guards.
        if getattr(page, "_already_attached", False):
            return
        page._already_attached = True
        def on_console(m: ConsoleMessage) -> None:
            if m.type in ("error", "warning"):
                self.entries.append({"url": url, "type": m.type, "text": m.text})

        def on_page_error(err: Exception) -> None:
            self.entries.append({"url": url, "type": "page-error", "text": str(err)})

        page.on("console", on_console)
        page.on("pageerror", on_page_error)


def audit_page(page: Page, url: str, slug: str, sink: ConsoleSink, viewport: str) -> dict:
    """Navigate, wait for network idle, screenshot, and snapshot the
    cmdk-overlay visibility — the operator-reported regression."""
    sink.attach(page, url)
    resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
    status = resp.status if resp else 0
    title = page.title()
    # cmdk overlay visibility check — the operator's bug. The overlay
    # exists in DOM on every page (layout.html); we want hidden=true.
    cmdk_state = page.evaluate("""
      () => {
        const o = document.querySelector('.v3-cmdk-overlay');
        if (!o) return {present: false};
        const r = o.getBoundingClientRect();
        const cs = getComputedStyle(o);
        return {
          present: true,
          hidden_attr: o.hidden,
          display: cs.display,
          visibility: cs.visibility,
          bounding_height: r.height,
          visible_to_user: cs.display !== 'none' && cs.visibility !== 'hidden' && r.height > 0,
        };
      }
    """)
    # Hero sentence presence on devices list
    hero = page.evaluate("""
      () => {
        const s = document.querySelector('.v3-hero-sentence');
        return s ? s.textContent.trim() : null;
      }
    """)
    # Top-nav count
    nav_count = page.evaluate("""
      () => document.querySelectorAll('nav.topnav a').length
    """)
    # Skip-link presence
    has_skip = page.evaluate("""
      () => !!document.querySelector('a.skip-link')
    """)
    screenshot_path = OUT / f"{viewport}-{slug}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    return {
        "url": url,
        "viewport": viewport,
        "status": status,
        "title": title,
        "cmdk": cmdk_state,
        "hero_sentence": hero,
        "topnav_count": nav_count,
        "has_skip_link": has_skip,
        "screenshot": str(screenshot_path.relative_to(OUT.parent)),
    }


def behavior_tests(page: Page, sink: ConsoleSink) -> list[dict]:
    """Specific behavioural assertions the operator hit + likely
    siblings. Each returns {test, passed, detail}."""
    results: list[dict] = []

    # 1. cmdk does NOT open on plain page load.
    page.goto(f"{HUB}/app/devices", wait_until="domcontentloaded")
    sink.attach(page, f"{HUB}/app/devices")
    cmdk_state = page.evaluate("""
      () => {
        const o = document.querySelector('.v3-cmdk-overlay');
        if (!o) return {present: false};
        const cs = getComputedStyle(o);
        return {hidden_attr: o.hidden, display: cs.display, visible: cs.display !== 'none'};
      }
    """)
    results.append({
        "test": "cmdk hidden on page load (operator-reported)",
        "passed": not cmdk_state.get("visible", True),
        "detail": cmdk_state,
    })

    # 2. ⌘K (Ctrl+K) opens the palette.
    page.keyboard.press("Control+k")
    page.wait_for_timeout(150)
    is_open = page.evaluate("""
      () => {
        const o = document.querySelector('.v3-cmdk-overlay');
        if (!o) return false;
        const cs = getComputedStyle(o);
        return cs.display !== 'none' && !o.hidden;
      }
    """)
    results.append({"test": "Ctrl+K opens palette", "passed": is_open, "detail": {"is_open": is_open}})

    # 3. Escape closes the palette (operator-reported lock-out).
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    is_closed = page.evaluate("""
      () => {
        const o = document.querySelector('.v3-cmdk-overlay');
        if (!o) return true;
        return o.hidden;
      }
    """)
    results.append({"test": "Escape closes palette (operator-reported)", "passed": is_closed, "detail": {"is_hidden": is_closed}})

    # 4. Skip-link receives focus first when Tab is pressed.
    page.keyboard.press("Tab")
    page.wait_for_timeout(100)
    first_focus = page.evaluate("""
      () => {
        const e = document.activeElement;
        return e ? {tag: e.tagName, cls: e.className, href: e.getAttribute && e.getAttribute('href')} : null;
      }
    """)
    results.append({
        "test": "Skip-link is first tab target (a11y)",
        "passed": (first_focus or {}).get("cls") == "skip-link",
        "detail": first_focus,
    })

    # 5. Brand link points at the dashboard.
    brand_href = page.evaluate("""
      () => {
        const b = document.querySelector('.brand');
        return b ? b.getAttribute('href') : null;
      }
    """)
    results.append({
        "test": "Brand link points at /app/",
        "passed": brand_href is not None and brand_href.endswith("/app/"),
        "detail": {"href": brand_href},
    })

    # 6. Search endpoint returns pages + at least one item.
    api_resp = page.request.get(f"{HUB}/api/v1/admin/search?q=Devices")
    api_json = api_resp.json() if api_resp.ok else None
    api_pages = []
    if api_json and api_json.get("ok"):
        api_pages = [i for i in api_json["data"]["items"] if i["kind"] == "page"]
    results.append({
        "test": "cmdk feeder /api/v1/admin/search returns matches",
        "passed": api_resp.ok and len(api_pages) > 0,
        "detail": {"status": api_resp.status, "page_count": len(api_pages)},
    })

    # 7. Device detail page renders the primary-action hero.
    devs = page.request.get(f"{HUB}/api/v1/admin/devices?show_qa_fixtures=0").json()
    real_devs = [d for d in devs["data"]["devices"] if not d.get("is_qa_fixture")]
    if real_devs:
        dev_id = real_devs[0]["id"]
        page.goto(f"{HUB}/app/devices/{dev_id}", wait_until="domcontentloaded")
        sink.attach(page, f"{HUB}/app/devices/{dev_id}")
        has_hero = page.evaluate("() => !!document.querySelector('.v3-detail-hero')")
        has_primary_btn = page.evaluate("() => !!document.querySelector('.v3-detail-hero-action button, .v3-detail-hero-action a')")
        results.append({
            "test": "Device detail hero renders with a primary action",
            "passed": has_hero and has_primary_btn,
            "detail": {"hero": has_hero, "primary_action": has_primary_btn, "device_id": dev_id},
        })
        # Screenshot the post-hotfix detail page too
        page.screenshot(path=str(OUT / "desktop-device-detail.png"), full_page=True)
    else:
        results.append({"test": "Device detail hero", "passed": False, "detail": "no real devices to test against"})

    return results


def main() -> int:
    cookie = read_session_cookie()
    sink = ConsoleSink()
    pages_to_audit = [
        ("/app/",            "status-dashboard"),
        ("/app/devices",     "devices-list"),
        ("/app/rules",       "rules"),
        ("/app/history",     "history"),
        ("/app/settings",    "settings"),
        ("/app/firmware",    "firmware"),
        ("/app/power",       "power"),
    ]
    summary = {"desktop": [], "mobile": [], "behavior": []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # Desktop pass
        ctx_d = browser.new_context(viewport={"width": 1440, "height": 900})
        attach_cookie(ctx_d, cookie)
        page_d = ctx_d.new_page()
        for path, slug in pages_to_audit:
            try:
                summary["desktop"].append(audit_page(page_d, HUB + path, slug, sink, "desktop"))
            except Exception as e:
                summary["desktop"].append({"url": HUB + path, "error": str(e)})
        # Behaviour pass (still desktop ctx)
        try:
            summary["behavior"] = behavior_tests(page_d, sink)
        except Exception as e:
            summary["behavior"] = [{"test": "(harness)", "passed": False, "detail": f"crashed: {e}"}]
        ctx_d.close()

        # Mobile pass — viewport-only; we only check the highest-leverage pages.
        ctx_m = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2, is_mobile=True, has_touch=True)
        attach_cookie(ctx_m, cookie)
        page_m = ctx_m.new_page()
        for path, slug in [("/app/", "status-dashboard"), ("/app/devices", "devices-list")]:
            try:
                summary["mobile"].append(audit_page(page_m, HUB + path, slug, sink, "mobile"))
            except Exception as e:
                summary["mobile"].append({"url": HUB + path, "error": str(e)})
        ctx_m.close()
        browser.close()

    summary["console_entries"] = sink.entries
    (OUT / "report.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))

    # Surface pass/fail counts
    behaviour_failed = [b for b in summary["behavior"] if not b.get("passed", False)]
    print(f"\n=== behaviour summary: {len(summary['behavior']) - len(behaviour_failed)} / {len(summary['behavior'])} passed ===")
    for b in behaviour_failed:
        print(f"  FAIL: {b['test']} — {b.get('detail')}")
    print(f"=== console errors/warnings: {len(sink.entries)} ===")
    return 1 if behaviour_failed else 0


if __name__ == "__main__":
    sys.exit(main())
