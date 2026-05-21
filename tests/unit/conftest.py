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

org-boundary phase 3: `organization_id` is now NOT NULL on most Tier-A
tables, so a Tier-A row can no longer be created ownerless. Production
always has a tenant and binds an org scope on every request, so
`hub_db` mirrors that: it seeds one default `Organization` and runs the
test inside `tenant_scope.org_context(<default org>)`. The
`before_flush` write-stamping then stamps `organization_id` on every
Tier-A row a test creates — pre-phase-3 service tests that never
mention organizations keep working unchanged. Tests that need to
control the tenant scope themselves (the org-isolation suite) use
`hub_db_unscoped`, which leaves the scope unset.
"""

from __future__ import annotations

from dataclasses import replace

import flask
import pytest

from app.config import load_settings
from app.db import get_engine, init_engine, session_scope
from app.models import Base


def pytest_collection_modifyitems(items):
    """Auto-tag every test under tests/unit/ with the `ci` marker."""
    for item in items:
        if "/unit/" in str(getattr(item, "path", "")).replace("\\", "/"):
            item.add_marker(pytest.mark.ci)


def _fresh_hub_db(tmp_path, name):
    """Point the process at a throwaway SQLite file and build the schema
    fresh. Returns the Settings."""
    settings = replace(
        load_settings(),
        database_url=f"sqlite:///{tmp_path / name}",
    )
    init_engine(settings)
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return settings


@pytest.fixture
def hub_db_unscoped(tmp_path):
    """An isolated SQLite hub database + a bare Flask app context, with
    NO tenant scope bound.

    Same as `hub_db` but without the ambient default-org context — used
    by the org-isolation suite, which sets `tenant_scope` scopes itself
    and asserts on the unscoped (`current_org() is None`) state.
    """
    settings = _fresh_hub_db(tmp_path, "rebooter-unit.sqlite")
    with flask.Flask(__name__).app_context():
        yield settings


@pytest.fixture
def hub_db(tmp_path):
    """An isolated SQLite hub database + a bare Flask app context,
    pre-bound to a default `Organization`.

    `init_engine` points the process at a throwaway SQLite file;
    `create_all` builds the schema fresh; the app context exists
    because some services reach for Flask `g`.

    org-boundary phase 3: a default `Organization` is seeded and the
    test body runs inside `tenant_scope.org_context(<default org>)`, so
    Tier-A rows a test creates are stamped with that org by the
    `before_flush` hook (production always has a bound tenant). The
    org-isolation tests, which need the unscoped state, use
    `hub_db_unscoped` instead.
    """
    from app.models import Organization
    from app.services import tenant_scope

    settings = _fresh_hub_db(tmp_path, "rebooter-unit.sqlite")
    with flask.Flask(__name__).app_context():
        with tenant_scope.system():
            with session_scope() as s:
                org = Organization(
                    name="Test Organization", slug="test-org",
                    status="active", plan="legacy",
                    is_self_hosted_default=True,
                )
                s.add(org)
                s.flush()
                org_id = org.id
        with tenant_scope.org_context(org_id):
            yield settings


@pytest.fixture
def hub_db_pre_org_constraints(tmp_path):
    """An isolated SQLite hub database built with the *pre-phase-3*
    schema — every Tier-A `organization_id` column NULLABLE.

    The org-boundary rollout (design §8.1) runs the
    `ensure_default_organization_backfill()` (step 3) BEFORE the
    constraint-hardening migration that flips `organization_id` to
    NOT NULL (step 4). So at backfill time the columns are nullable and
    carry NULL rows for the backfill to stamp. The default `hub_db`
    fixture builds the final (hardened) schema, where an ownerless
    Tier-A row cannot exist — which is correct for every test EXCEPT
    the backfill tests, whose whole point is to exercise the
    NULL-stamping step.

    This fixture reproduces the production pre-step-4 state: it clones
    `Base.metadata`, marks every Tier-A `organization_id` column
    nullable, and `create_all`s from the clone. The model classes are
    untouched (still NOT NULL) — only this throwaway DB schema differs.
    """
    import sqlalchemy as sa

    settings = replace(
        load_settings(),
        database_url=f"sqlite:///{tmp_path / 'rebooter-unit-prc.sqlite'}",
    )
    init_engine(settings)
    engine = get_engine()

    pre_meta = sa.MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(pre_meta)
    for table in pre_meta.tables.values():
        col = table.c.get("organization_id")
        if col is not None:
            col.nullable = True

    pre_meta.drop_all(engine)
    pre_meta.create_all(engine)
    with flask.Flask(__name__).app_context():
        yield settings
