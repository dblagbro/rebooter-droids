"""Responsive / mobile-tablet smoke tests.

Validates the v0.3 CSS-first responsive pass:
- No horizontal page scroll at mobile (375px) or tablet (768px) widths.
- Topbar nav reachable (collapses to a horizontal-scroll strip on mobile,
  wraps on tablet — every nav link is still focusable).
- Dashboard `.stat-grid` collapses to a single column on mobile.
- Wide tables (`devices_list`, `events`, `audit_list`) scroll inside
  their `.table-wrap` container, not the page.
- No console errors and no 5xx responses while loading the core pages.

These tests run against the live deployment by default (same as the rest
of the QA suite) — set `REBOOTER_QA_BASE` to point them elsewhere.
"""

import pytest

# v0.5.98 (P-QA gate-3): the browser tests skip cleanly when playwright
# isn't installed (or the chromium binary is missing) — the
# `chromium_browser` fixture handles both — so this file is safe to
# gate. The CI gate runs without browsers and these all skip.
pytestmark = pytest.mark.ci


# ─── helpers ────────────────────────────────────────────────────────────────

def _page_overflows_horizontally(page) -> bool:
    """True iff `<html>` has horizontal overflow at the current viewport."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth > "
        "document.documentElement.clientWidth + 1"
    )


def _viewport(page) -> dict:
    return page.viewport_size or {"width": 0, "height": 0}


def _no_console_errors(p) -> None:
    errs = getattr(p, "_console_errors", [])
    # Filter out third-party noise that's not a regression — there
    # currently isn't any but the asserter shape stays for future bumps.
    real = [(t, msg) for (t, msg) in errs]
    assert not real, f"console errors at {p.url}: {real}"


def _no_5xx(p) -> None:
    fails = getattr(p, "_failed_responses", [])
    assert not fails, f"5xx responses at {p.url}: {fails}"


# ─── mobile (375×667) ──────────────────────────────────────────────────────

def test_mobile_login_page_no_horizontal_scroll(mobile_page, base_url):
    mobile_page.goto(f"{base_url}/app/login")
    mobile_page.wait_for_load_state("networkidle")
    assert _viewport(mobile_page)["width"] == 375
    assert not _page_overflows_horizontally(mobile_page), (
        "login page has horizontal overflow at 375px width — "
        "form input or button is exceeding the viewport"
    )
    _no_console_errors(mobile_page)
    _no_5xx(mobile_page)


def test_mobile_dashboard_no_horizontal_scroll(mobile_logged_in_page):
    p = mobile_logged_in_page
    assert not _page_overflows_horizontally(p), (
        "dashboard has horizontal overflow at 375px — most likely a "
        "wide table or the topbar push-out is the culprit"
    )
    _no_console_errors(p)
    _no_5xx(p)


def test_mobile_stat_grid_collapses_to_one_column(mobile_logged_in_page):
    """At ≤640px the @media block forces grid-template-columns: 1fr."""
    p = mobile_logged_in_page
    cols = p.evaluate(
        "() => {"
        "  const g = document.querySelector('.stat-grid');"
        "  if (!g) return null;"
        "  return getComputedStyle(g).gridTemplateColumns.split(' ').length;"
        "}"
    )
    assert cols == 1, (
        f"expected 1 column on mobile dashboard, got {cols} — "
        f"check the @media (max-width: 640px) block in app.css"
    )


# 0.6.45 Batch E: split the original
# `test_mobile_topbar_nav_links_reachable` into two distinct tests so a
# CI failure points at one bucket (.topnav vs .bottomnav) instead of
# making the operator dig through a combined trace. PR-9 (0.6.29) trims
# both navs from 6 items to 4 when `ui_nav_v2=1` (default in prod);
# the legacy 6-item shape remains supported via REBOOTER_UI_NAV_V2=0.
# Status is reached via the brand link; Power via ⌘K or device-detail.
# These now live in the file-level `pytest.mark.ci` bucket so the gate
# catches future regressions before they ship (BUG-065 was missed
# pre-v0.5.98 because the `responsive` marker was outside `-m ci`).
def test_mobile_topnav_link_count(mobile_logged_in_page):
    p = mobile_logged_in_page
    top_nav_count = p.locator(".topnav a").count()
    assert top_nav_count in (4, 6), (
        f"expected 4 (nav_v2 on) or 6 (nav_v2 off) desktop top-nav "
        f"links, got {top_nav_count}"
    )


def test_mobile_bottomnav_link_count(mobile_logged_in_page):
    p = mobile_logged_in_page
    bottom_nav_count = p.locator(".bottomnav a").count()
    assert bottom_nav_count in (4, 6), (
        f"expected 4 (nav_v2 on) or 6 (nav_v2 off) mobile bottom-nav "
        f"links, got {bottom_nav_count}"
    )


def test_mobile_devices_list_table_wrap_scrolls(mobile_logged_in_page, base_url):
    """Wide tables must scroll inside `.table-wrap`, never push the
    page itself wider than the viewport.

    The empty-state renders without a `.table-wrap` wrapper (because
    there's no table to wrap), so we only assert the wrapper exists
    when the fleet has at least one device."""
    p = mobile_logged_in_page
    p.goto(f"{base_url}/app/devices?show_qa_fixtures=1")
    p.wait_for_load_state("networkidle")
    assert not _page_overflows_horizontally(p), (
        "devices list page is overflowing horizontally — table-wrap "
        "didn't contain the table scroll"
    )
    # If the fleet is non-empty, .table-wrap MUST be present.
    if "v3-empty-state" in p.content():
        return
    wraps = p.locator(".table-wrap").count()
    assert wraps >= 1, "devices list missing .table-wrap container"


# ─── tablet (768×1024) ─────────────────────────────────────────────────────

def test_tablet_login_page_no_horizontal_scroll(tablet_page, base_url):
    tablet_page.goto(f"{base_url}/app/login")
    tablet_page.wait_for_load_state("networkidle")
    assert _viewport(tablet_page)["width"] == 768
    assert not _page_overflows_horizontally(tablet_page)
    _no_console_errors(tablet_page)
    _no_5xx(tablet_page)


def test_tablet_dashboard_no_horizontal_scroll(tablet_logged_in_page):
    p = tablet_logged_in_page
    assert not _page_overflows_horizontally(p)
    _no_console_errors(p)
    _no_5xx(p)


def test_tablet_stat_grid_has_multiple_columns(tablet_logged_in_page):
    """Tablet width should retain the auto-fit minmax(200px, 1fr) grid
    — i.e. NOT the mobile single-column stack. This guards against
    accidentally promoting the mobile breakpoint past 640px."""
    p = tablet_logged_in_page
    cols = p.evaluate(
        "() => {"
        "  const g = document.querySelector('.stat-grid');"
        "  if (!g) return null;"
        "  return getComputedStyle(g).gridTemplateColumns.split(' ').length;"
        "}"
    )
    assert cols and cols >= 2, (
        f"expected ≥2 columns on tablet dashboard, got {cols} — "
        f"mobile breakpoint may have leaked past 640px"
    )


def test_tablet_events_page_no_horizontal_scroll(tablet_logged_in_page, base_url):
    p = tablet_logged_in_page
    p.goto(f"{base_url}/app/events")
    p.wait_for_load_state("networkidle")
    assert not _page_overflows_horizontally(p)


# ─── parameterised cross-viewport regression ───────────────────────────────

@pytest.mark.parametrize(
    "fixture_name,path",
    [
        ("mobile_logged_in_page", "/app/devices"),
        ("mobile_logged_in_page", "/app/events"),
        ("mobile_logged_in_page", "/app/audit"),
        ("mobile_logged_in_page", "/app/users"),
        ("tablet_logged_in_page", "/app/devices"),
        ("tablet_logged_in_page", "/app/events"),
        ("tablet_logged_in_page", "/app/audit"),
        ("tablet_logged_in_page", "/app/users"),
    ],
)
def test_table_pages_no_page_overflow(request, fixture_name, path, base_url):
    """Every list-style admin page must keep page-level horizontal
    overflow off across mobile + tablet. Internal table-wrap scroll is
    fine and expected."""
    p = request.getfixturevalue(fixture_name)
    p.goto(f"{base_url}{path}")
    p.wait_for_load_state("networkidle")
    assert not _page_overflows_horizontally(p), (
        f"{fixture_name} on {path}: page overflows horizontally"
    )
