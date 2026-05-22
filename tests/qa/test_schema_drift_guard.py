"""CI schema-drift guard — the permanent guard against a v0.6.0-class bug.

THE BUG CLASS
=============
The hub does NOT run Alembic at runtime. Production schema is managed
entirely by a hand-rolled startup bootstrap — `app/services/bootstrap.py`
`run_startup_bootstrap()` -> `ensure_schema()`:

  * `Base.metadata.create_all()` — `CREATE TABLE IF NOT EXISTS` per table.
    It NEVER issues `ALTER TABLE` for a new column on a table that
    already exists.
  * `_ensure_columns()` — a HAND-MAINTAINED `ADD COLUMN IF NOT EXISTS`
    list (`_PENDING_COLUMNS`).
  * `_ensure_org_id_columns()` — adds `organization_id` to a
    HAND-MAINTAINED list of pre-existing tables (`_ORG_ID_PREEXISTING_TABLES`).
  * `_ensure_constraints()` — post-backfill `ALTER COLUMN ... SET NOT NULL`.

The v0.6.0 incident: `organization_id` columns were added to the
SQLAlchemy MODELS. On a FRESH database `create_all()` builds every table
WITH the column, so fresh-DB tests and the SQLite unit suite were all
green. But on a PRE-EXISTING production database the table already
exists, `create_all()` is a no-op for it, and the hand-maintained
upgrade list had not been updated — so the column was never added.
Result: `column organization_id does not exist`, hub-wide 500s.

The gap is precisely: a model column / table / constraint that the
bootstrap's idempotent UPGRADE path does not know to create on an
ALREADY-EXISTING database.

THE GUARD
=========
This test catches exactly that. It:

  1. Spins up a throwaway Postgres (production is Postgres; the
     bootstrap has dialect-specific paths — `pg_advisory_lock`,
     `ADD COLUMN IF NOT EXISTS`, `information_schema` constraint checks
     — that an in-process SQLite test can never exercise).
  2. Builds a *pre-existing / older* database: `create_all()` the full
     current schema, then DROPs a representative set of model columns —
     every droppable column the bootstrap's own upgrade lists
     (`_PENDING_COLUMNS` + the `organization_id` columns) claim to
     manage. A column dropped here that the bootstrap does not re-add is
     exactly a v0.6.0-class gap.
  3. Runs the FULL production `run_startup_bootstrap()` against it.
  4. Reflects the resulting LIVE Postgres schema and asserts it matches
     `Base.metadata` — every model table present, every model column
     present.

It deliberately accounts for the bootstrap's add-nullable-then-backfill-
then-constrain approach (`_PENDING_CONSTRAINTS`) so that is not falsely
flagged as drift: a column the bootstrap legitimately adds nullable and
later hardens is checked for PRESENCE, and its final nullability is
checked against the post-backfill expectation, not the raw model flag.

It generalises `tests/unit/test_bootstrap_org_schema_upgrade.py`, which
proved the pattern for `organization_id` only, on SQLite. This guard
covers EVERY managed column and runs on real Postgres inside the CI
gate, so the next time a model gains a column without a matching
bootstrap upgrade entry, CI goes red here instead of production.

WHERE IT RUNS
=============
Marked `ci`, so it runs in the `-m ci` GitHub Actions gate. The gate's
`rd-ci-pg` container is only reachable inside the `rd-ci` Docker network
(no host port mapping), so this test starts its OWN throwaway Postgres
container via `docker run` — self-contained and deterministic. If Docker
is unavailable (a bare local checkout) it skips cleanly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import replace

import flask
import pytest
import sqlalchemy as sa

from app.config import load_settings
from app.db import get_engine, init_engine
from app.models import Base

pytestmark = pytest.mark.ci


# ── Throwaway Postgres ──────────────────────────────────────────────────
#
# The bootstrap is dialect-specific, so the guard MUST run on Postgres.
# A dedicated, disposable container keeps the guard deterministic and
# independent of whatever else the CI gate has running.

_PG_IMAGE = "postgres:16"
_PG_USER = "driftguard"
_PG_PASSWORD = "driftguard-ci"
_PG_DB = "driftguard"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="module")
def drift_postgres_url() -> str:
    """Start a throwaway Postgres container and yield a SQLAlchemy URL.

    Module-scoped — one container for the whole file. Removed on teardown
    regardless of test outcome. Skips cleanly when Docker is unavailable.
    """
    if not _docker_available():
        pytest.skip("docker unavailable — schema-drift guard needs Postgres")

    name = f"rd-driftguard-pg-{uuid.uuid4().hex[:10]}"
    # An ephemeral host port — let Docker pick a free one so parallel
    # runs never collide.
    proc = subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", name,
            "-e", f"POSTGRES_USER={_PG_USER}",
            "-e", f"POSTGRES_PASSWORD={_PG_PASSWORD}",
            "-e", f"POSTGRES_DB={_PG_DB}",
            "-P",  # publish container ports to ephemeral host ports
            _PG_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.skip(
            f"could not start throwaway Postgres: {proc.stderr.strip()}"
        )

    try:
        # Resolve the ephemeral host port Docker assigned to 5432.
        port_proc = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            capture_output=True, text=True, timeout=30,
        )
        # Output looks like "0.0.0.0:49153" (possibly multiple lines).
        host_port = port_proc.stdout.strip().splitlines()[0].rsplit(":", 1)[-1]

        # Wait for Postgres to accept connections — deterministic, bounded.
        ready = False
        for _ in range(60):
            r = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", _PG_USER],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0:
                ready = True
                break
            time.sleep(1)
        if not ready:
            pytest.skip("throwaway Postgres did not become ready in time")

        url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@127.0.0.1:{host_port}/{_PG_DB}"
        )
        yield url
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, timeout=60,
        )


# ── What the bootstrap's UPGRADE path claims to manage ──────────────────
#
# These are the columns a v0.6.0-class bug lives in: the bootstrap's
# hand-maintained upgrade lists. The guard simulates an old DB by
# dropping exactly these, then proves the bootstrap re-adds every one.


def _managed_pending_columns() -> set[tuple[str, str]]:
    """`(table, column)` pairs from `bootstrap._PENDING_COLUMNS`."""
    from app.services.bootstrap import _PENDING_COLUMNS

    return {(t, c) for (t, c, _ddl) in _PENDING_COLUMNS}


def _managed_org_id_columns() -> set[tuple[str, str]]:
    """`(table, 'organization_id')` for every pre-existing org table the
    bootstrap claims to upgrade — the exact v0.6.0 incident set."""
    from app.services.bootstrap import _ORG_ID_PREEXISTING_TABLES

    return {(t, "organization_id") for t in _ORG_ID_PREEXISTING_TABLES}


def _post_backfill_not_null_columns() -> set[tuple[str, str]]:
    """`(table, column)` the bootstrap deliberately adds NULLABLE and
    only later hardens to NOT NULL via `_ensure_constraints()`.

    Their FINAL state still matches the model (NOT NULL), so they are
    NOT drift — but the guard must understand the add-nullable-then-
    constrain sequence to compare nullability correctly.
    """
    from app.services.bootstrap import _PENDING_CONSTRAINTS

    out: set[tuple[str, str]] = set()
    for table, check_query, _ddl in _PENDING_CONSTRAINTS:
        # The check query names the column it hardens.
        if "column_name = '" in check_query:
            col = check_query.split("column_name = '", 1)[1].split("'", 1)[0]
            out.add((table, col))
    return out


def _model_columns() -> dict[str, dict[str, sa.Column]]:
    """`{table_name: {column_name: Column}}` from `Base.metadata`."""
    return {
        name: {c.name: c for c in table.columns}
        for name, table in Base.metadata.tables.items()
    }


def _droppable(table_name: str, column: sa.Column) -> bool:
    """A column the guard may DROP to simulate an older DB.

    Skip primary-key columns (dropping them is meaningless and would
    require rebuilding the table). Everything else is fair game — the
    bootstrap's upgrade path must be able to bring any of them back on a
    pre-existing database.
    """
    return not column.primary_key


# ── The guard ───────────────────────────────────────────────────────────


def _build_aged_database(database_url: str) -> list[tuple[str, str]]:
    """Create the full current schema on `database_url`, then DROP a
    representative set of managed columns to simulate a PRE-EXISTING,
    older production database.

    Returns the sorted list of `(table, column)` pairs dropped — the
    bootstrap is then expected to re-add every one of them.

    The set dropped is the UNION of:
      * every `(table, column)` in `_PENDING_COLUMNS` — the hand-
        maintained `ADD COLUMN` list, and
      * `organization_id` on every table in `_ORG_ID_PREEXISTING_TABLES`
        — the exact column set of the v0.6.0 incident,
    intersected with columns that actually exist on a current model
    table and are droppable (not a primary key).

    `DROP COLUMN ... CASCADE` removes any index/constraint that depended
    on the column, faithfully reproducing a database built before the
    column existed.
    """
    settings = replace(load_settings(), database_url=database_url)
    init_engine(settings)
    engine = get_engine()

    # Start from a genuinely clean database, then build the full,
    # current schema — this is the "table already exists" precondition.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    model_cols = _model_columns()
    claimed = _managed_pending_columns() | _managed_org_id_columns()

    to_drop: list[tuple[str, str]] = []
    for table, column in sorted(claimed):
        cols = model_cols.get(table)
        if cols is None or column not in cols:
            # The bootstrap upgrade list names a table/column the models
            # no longer have — stale list entry, not this guard's job.
            continue
        if not _droppable(table, cols[column]):
            continue
        to_drop.append((table, column))

    with engine.begin() as conn:
        for table, column in to_drop:
            conn.execute(
                sa.text(
                    f'ALTER TABLE "{table}" '
                    f'DROP COLUMN IF EXISTS "{column}" CASCADE'
                )
            )

    return to_drop


def _live_columns(engine) -> dict[str, dict[str, dict]]:
    """Reflect the live database: `{table: {column: info_dict}}`."""
    inspector = sa.inspect(engine)
    out: dict[str, dict[str, dict]] = {}
    for table in inspector.get_table_names():
        out[table] = {
            c["name"]: c for c in inspector.get_columns(table)
        }
    return out


def test_schema_drift_guard_pending_lists_are_not_empty():
    """Sanity: the bootstrap upgrade lists actually name columns.

    If `_PENDING_COLUMNS` / `_ORG_ID_PREEXISTING_TABLES` were emptied,
    the main guard below would drop nothing and pass vacuously. This
    keeps the guard honest.
    """
    claimed = _managed_pending_columns() | _managed_org_id_columns()
    assert len(claimed) >= 20, (
        "the bootstrap upgrade lists name suspiciously few columns — "
        f"got {len(claimed)}; the drift guard would run near-vacuous"
    )


def test_bootstrap_upgrades_an_aged_database_to_match_the_models(
    drift_postgres_url,
):
    """THE GUARD.

    Simulate a pre-existing/older production database (full schema, then
    every managed column dropped), run the full production bootstrap,
    and assert the resulting LIVE Postgres schema matches the SQLAlchemy
    models — every model table and every model column present.

    This is RED whenever a model column exists that the bootstrap's
    idempotent upgrade path does not know to create on an already-
    existing database — i.e. exactly the v0.6.0 bug class.
    """
    from app.services.bootstrap import run_startup_bootstrap

    # ── 1. Build the aged (pre-existing) database. ───────────────────
    dropped = _build_aged_database(drift_postgres_url)
    assert dropped, (
        "the guard dropped no columns — it would pass vacuously; the "
        "bootstrap upgrade lists name nothing droppable"
    )
    engine = get_engine()

    # Sanity: the database really is in the aged shape — every dropped
    # column is genuinely missing right now.
    pre = _live_columns(engine)
    for table, column in dropped:
        assert table in pre, f"aged DB missing whole table {table!r}"
        assert column not in pre[table], (
            f"aged DB unexpectedly still has {table}.{column}"
        )

    # ── 2. Run the FULL production bootstrap against it. ─────────────
    # `run_startup_bootstrap` runs `ensure_schema` (create_all +
    # _ensure_columns + _ensure_org_id_columns), the backfills, and
    # `_ensure_constraints` — the complete production startup path.
    settings = replace(load_settings(), database_url=drift_postgres_url)
    with flask.Flask(__name__).app_context():
        run_startup_bootstrap(settings)

    # ── 3. Assert the live schema matches Base.metadata. ─────────────
    live = _live_columns(get_engine())
    model_cols = _model_columns()
    post_backfill_nn = _post_backfill_not_null_columns()

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    nullability_drift: list[str] = []

    for table_name, columns in model_cols.items():
        if table_name not in live:
            missing_tables.append(table_name)
            continue
        live_cols = live[table_name]
        for col_name, model_col in columns.items():
            if col_name not in live_cols:
                missing_columns.append(f"{table_name}.{col_name}")
                continue
            # Nullability: a column the model declares NOT NULL must end
            # up NOT NULL in the live DB — UNLESS it is on the bootstrap's
            # deliberate add-nullable-then-backfill-then-constrain path,
            # in which case the bootstrap should still have hardened it
            # by the time `_ensure_constraints()` has run. Either way the
            # FINAL state must match the model, so this is checked for
            # every NOT NULL model column. (A column the bootstrap leaves
            # permanently nullable but the model marks NOT NULL is real
            # drift and is reported.)
            model_not_null = not model_col.nullable
            live_nullable = live_cols[col_name]["nullable"]
            if model_not_null and live_nullable:
                tag = (
                    " (post-backfill constraint — bootstrap did not "
                    "harden it)"
                    if (table_name, col_name) in post_backfill_nn
                    else ""
                )
                nullability_drift.append(
                    f"{table_name}.{col_name}{tag}"
                )

    problems: list[str] = []
    if missing_tables:
        problems.append(
            "model tables the bootstrap did not create on an "
            f"already-existing database: {sorted(missing_tables)}"
        )
    if missing_columns:
        problems.append(
            "model COLUMNS the bootstrap did not add to an "
            "already-existing database (v0.6.0-class drift): "
            f"{sorted(missing_columns)}"
        )
    if nullability_drift:
        problems.append(
            "columns the model marks NOT NULL but the bootstrap left "
            f"nullable: {sorted(nullability_drift)}"
        )

    assert not problems, (
        "SCHEMA DRIFT — the startup bootstrap can no longer upgrade a "
        "pre-existing production database to match the SQLAlchemy "
        "models. This is a v0.6.0-class bug: a fresh DB would be fine "
        "(create_all builds every column) but a real upgraded "
        "production DB would be broken. Fix `app/services/bootstrap.py` "
        "(`_PENDING_COLUMNS` / `_ORG_ID_PREEXISTING_TABLES` / "
        "`ensure_schema`) so the upgrade path creates the column(s) "
        "below.\n\n  - " + "\n  - ".join(problems)
    )

    # ── 4. Every column the guard dropped was re-created. ────────────
    # Explicit, per-column confirmation — makes a failure name the exact
    # managed column the bootstrap forgot.
    still_missing = [
        f"{table}.{column}"
        for table, column in dropped
        if column not in live.get(table, {})
    ]
    assert not still_missing, (
        "the bootstrap upgrade path claims to manage these columns but "
        "did not re-create them on a pre-existing database: "
        f"{sorted(still_missing)}"
    )
