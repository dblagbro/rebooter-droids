"""Shared fixtures for the in-process unit-test tree.

`tests/unit/` exercises the service layer directly — no HTTP, no
Docker, no live deployment. Two tiers:

- *pure* — functions with no I/O (`_rules_forms` builders, schedule
  recurrence math). They need no fixture at all.
- *DB-backed* — service functions that touch the database. They take
  the `hub_db` fixture: an isolated SQLite schema built fresh per test.

Every test collected under `tests/unit/` is auto-tagged `ci` (see
`pytest_collection_modifyitems`) — unit tests are fast and
deterministic, so they always belong in the gate. New unit test files
need no per-file marker.
"""

from __future__ import annotations

from dataclasses import replace

import flask
import pytest

from app.config import load_settings
from app.db import get_engine, init_engine
from app.models import Base


def pytest_collection_modifyitems(items):
    """Auto-tag every test under tests/unit/ with the `ci` marker."""
    for item in items:
        if "/unit/" in str(getattr(item, "path", "")).replace("\\", "/"):
            item.add_marker(pytest.mark.ci)


@pytest.fixture
def hub_db(tmp_path):
    """An isolated SQLite hub database + a bare Flask app context.

    `init_engine` points the process at a throwaway SQLite file;
    `create_all` builds the schema fresh; the app context exists
    because some services reach for Flask `g`. Same pattern as the
    in-process tests under tests/qa (test_v0514 / v0536 / v0414 / v0417).
    """
    settings = replace(
        load_settings(),
        database_url=f"sqlite:///{tmp_path / 'rebooter-unit.sqlite'}",
    )
    init_engine(settings)
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with flask.Flask(__name__).app_context():
        yield settings
