"""Shared QA fixtures.

Suite targets the live deployment at https://www.voipguru.org/rebooter
unless REBOOTER_QA_BASE is overridden.
"""

import os
import re
import time

import pytest
import requests

BASE = os.environ.get("REBOOTER_QA_BASE", "https://www.voipguru.org/rebooter")
ADMIN_EMAIL = os.environ.get("REBOOTER_QA_EMAIL", "dblagbro@gmail.com")
ADMIN_PASS = os.environ.get(
    "REBOOTER_QA_PASS", "Super*120120"
)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE


@pytest.fixture(scope="session")
def admin_creds():
    return ADMIN_EMAIL, ADMIN_PASS


@pytest.fixture(scope="session")
def admin_token(base_url, admin_creds) -> str:
    email, pw = admin_creds
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def chromium_browser():
    """Headless Chromium tied to the playwright-installed binary."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=os.environ.get(
                "PLAYWRIGHT_CHROMIUM_PATH",
                "/home/dblagbro/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
            ),
            args=["--no-sandbox"],
        )
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(chromium_browser):
    ctx = chromium_browser.new_context()
    p = ctx.new_page()
    p._console_errors = []  # type: ignore
    p._failed_responses = []  # type: ignore
    p.on(
        "console",
        lambda m: p._console_errors.append((m.type, m.text))  # type: ignore
        if m.type in ("error",)
        else None,
    )
    p.on(
        "response",
        lambda r: p._failed_responses.append((r.status, r.request.method, r.url))  # type: ignore
        if r.status >= 500
        else None,
    )
    yield p
    ctx.close()


@pytest.fixture
def logged_in_page(page, base_url, admin_creds):
    """Pre-authenticated page object."""
    email, pw = admin_creds
    page.goto(f"{base_url}/app/login")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', pw)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "/app/" in page.url and "/login" not in page.url, (
        f"login failed; landed at {page.url}"
    )
    return page


def unique_suffix() -> str:
    return f"{int(time.time() * 1000) % 100000:05d}"
