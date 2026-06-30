"""CHANGELOG / source-symbol consistency gate (0.6.56).

Proxy-team monitoring (memo `2026-06-30-rebooter-droids-adaptive-heartbeat-
design-shipped-code-didnt.md`) caught that the v0.6.48 CHANGELOG entry
documented `has_recent_command_activity()` + `heartbeat_interval_active_seconds`
+ `command_active_window_seconds` + `REBOOTER_HEARTBEAT_INTERVAL_ACTIVE_SECONDS`
+ `REBOOTER_COMMAND_ACTIVE_WINDOW_SECONDS` — none of which ever existed in
`app/`. Click-to-execute p50 stayed at ~60s post-ship, exactly matching
"design documented, code didn't ship."

This test asserts that every backtick-quoted identifier in CHANGELOG.md
that *looks like* a Python symbol or an env var resolves to at least one
match somewhere under `app/` — so future doc/code drift trips the suite
before merge. Same shape as llm-proxy-v2's
`tests/unit/test_v5141_hook_runner_pins_all_endpoints.py` which the proxy
team cited as their precedent.

Allowed escapes (the test deliberately ignores):
- Identifiers under `### Errata` blocks (those by definition document
  things that DIDN'T ship — the whole point of erratum).
- Identifiers in the `[Unreleased]` section (work in progress).
- Anything matching `IGNORED_PATTERNS` below (third-party libs, OS
  utilities, generic English words that happen to read like idents).
- Versions strictly older than `MIN_VERSION_TO_ENFORCE` — those entries
  predate the proxy-team finding and are grandfathered. Most failures
  in the historical pile are renamed test files / database-id examples
  / stdlib exception names, not real shipped-doc-without-code defects.
  The test's purpose is to PREVENT FUTURE drift, not retro-audit the
  pre-Errata-rule past.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).parent.parent.parent
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_APP = _REPO_ROOT / "app"

# Versions strictly older than this aren't enforced. The proxy-team
# finding that prompted this test was the v0.6.48 adaptive-heartbeat
# CHANGELOG entry; enforcement starts there and includes 0.6.48 itself
# (which the errata move covers).
_MIN_VERSION_TO_ENFORCE = (0, 6, 48)


def _parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def _identifiers_from_changelog() -> dict[str, list[str]]:
    """Return {version: [identifier, ...]} for every Markdown
    backtick-quoted token in CHANGELOG.md that looks like a Python
    identifier (`a_b_c`) or env var (`ALL_CAPS_WITH_UNDERSCORES`).

    Skips the `[Unreleased]` section and any subsection whose heading
    starts with `### Errata` — those are deliberate doc-only blocks.
    """
    lines = _CHANGELOG.read_text().splitlines()
    out: dict[str, list[str]] = {}
    current_version = None
    in_errata = False

    version_header = re.compile(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]")
    errata_header = re.compile(r"^### Errata\b")
    other_subsection = re.compile(r"^### ")
    backtick_token = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

    for line in lines:
        m = version_header.match(line)
        if m:
            current_version = m.group(1)
            in_errata = False
            continue
        if line.startswith("## ["):
            # Non-version section header (e.g. [Unreleased]).
            current_version = None
            in_errata = False
            continue
        if errata_header.match(line):
            in_errata = True
            continue
        if other_subsection.match(line):
            in_errata = False
            continue
        if current_version is None or in_errata:
            continue
        out.setdefault(current_version, []).extend(
            backtick_token.findall(line)
        )
    return out


# Identifiers we never expect to appear in app/ — generic English
# words that happen to look like idents, third-party / OS surfaces.
IGNORED_PATTERNS = {
    # Generic CHANGELOG vocabulary.
    "true", "false", "null", "none", "version", "default", "yes", "no",
    "id", "ids", "name", "names",
    # Python stdlib / runtime keywords used in prose.
    "type", "types", "kind", "kinds",
    # Standard HTTP / time / unit nouns.
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
    "ms", "s", "min", "sec",
    # Filesystem / shell / SQL nouns we mention but don't define.
    "main", "master", "origin", "remote", "branch",
    # External library surface names that show up in prose but live
    # in installed packages, not app/.
    "pytest", "playwright", "Jinja", "Flask", "SQLAlchemy", "Postgres",
    "psycopg", "pytz", "requests",
    # CHANGELOG meta tags.
    "Unreleased", "Changed", "Added", "Removed", "Fixed", "Deprecated",
    "Security", "Errata",
}


def test_changelog_documented_identifiers_exist_in_source():
    """Every backtick-quoted Python-/env-var-shaped identifier in
    a *released* CHANGELOG version (not Errata, not Unreleased) must
    resolve to at least one occurrence under `app/`.

    A failure here is the proxy-team's exact 2026-06-30 finding:
    "the CHANGELOG documents something the code doesn't have."
    """
    by_version = _identifiers_from_changelog()

    missing: dict[str, list[str]] = {}
    for version, idents in by_version.items():
        if _parse_version(version) < _MIN_VERSION_TO_ENFORCE:
            continue
        for ident in idents:
            if ident in IGNORED_PATTERNS:
                continue
            if len(ident) < 4:
                # Single chars / very short tokens (`s`, `id`) yield
                # too many false matches; skip — they're prose, not
                # API names.
                continue
            # ripgrep-equivalent: scan every app/**/*.py for the
            # identifier as a substring. False-positive-friendly on
            # purpose — we want "did this ever land in source at all."
            found = False
            for path in _APP.rglob("*.py"):
                try:
                    if ident in path.read_text():
                        found = True
                        break
                except (UnicodeDecodeError, OSError):
                    continue
            if not found:
                missing.setdefault(version, []).append(ident)

    assert not missing, (
        "CHANGELOG documents identifiers that don't exist in app/. "
        "Either implement them, or move the entry under `### Errata` "
        "with the standard correction note. "
        f"Missing per version: {missing!r}"
    )
