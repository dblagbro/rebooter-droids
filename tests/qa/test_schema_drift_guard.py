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

import ast
import pathlib
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


# ── What an "aged" production database looks like ───────────────────────
#
# The aged-DB simulation must be derived from a source that is
# INDEPENDENT of the bootstrap's hand-maintained upgrade lists
# (`_PENDING_COLUMNS` / `_ORG_ID_PREEXISTING_TABLES`). The v0.6.0 bug WAS
# those lists being incomplete relative to the models — a guard that
# decided what to drop FROM those same lists would be tautological: gut
# the list and the drop-set guts with it, so the gap is never exercised.
#
# The independent source of truth for "the schema a real upgraded
# production database already has" is Alembic migration
# `0001_baseline_baseline_schema` — the squashed baseline every
# production DB has applied. The guard parses that migration's
# `op.create_table(...)` calls (statically, via `ast` — no DB, no
# Alembic run) to learn the BASELINE `(table, column)` set.
#
#   * A model table NOT in the baseline is an org-era *new whole table*
#     (organizations, api_tokens, …) — `create_all()` builds it whole
#     when absent.
#   * A model COLUMN on a baseline table that is NOT in the baseline is
#     a post-baseline column: on a real upgraded DB `create_all()` is a
#     no-op for the existing table, so that column reaches production
#     ONLY via the bootstrap's `ALTER TABLE ADD COLUMN` upgrade path.
#     These are exactly the v0.6.0 surface, and the guard drops every
#     one of them — found from the models-vs-Alembic diff, never from
#     the bootstrap's own lists.


def _parse_baseline_schema() -> tuple[frozenset[str], frozenset[tuple[str, str]]]:
    """Statically parse Alembic `0001_baseline_baseline_schema` and
    return `(baseline_tables, baseline_columns)`.

    `baseline_columns` is the set of `(table, column)` pairs the
    baseline migration's `op.create_table(...)` calls declare. Parsed
    with `ast` — no database, no Alembic execution, fully deterministic.
    """
    # tests/qa/<thisfile> -> repo root -> migrations/versions/...
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    candidates = sorted(
        (repo_root / "migrations" / "versions").glob("0001_*.py")
    )
    if not candidates:
        raise RuntimeError(
            "could not locate Alembic migration 0001 — the schema-drift "
            "guard needs it as the baseline-schema source of truth"
        )
    tree = ast.parse(candidates[0].read_text())

    tables: set[str] = set()
    columns: set[tuple[str, str]] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                table = node.args[0].value
                tables.add(table)
                for arg in node.args[1:]:
                    if (
                        isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "Column"
                        and arg.args
                        and isinstance(arg.args[0], ast.Constant)
                    ):
                        columns.add((table, arg.args[0].value))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return frozenset(tables), frozenset(columns)


_BASELINE_TABLES, _BASELINE_COLUMNS = _parse_baseline_schema()


def _bootstrap_intended_not_null() -> dict[tuple[str, str], bool]:
    """For every column the bootstrap's UPGRADE path manages, the
    nullability the bootstrap is DESIGNED to leave it at on a
    pre-existing database — `True` = NOT NULL, `False` = nullable.

    The bootstrap's nullability is, by deliberate design, sometimes
    LOOSER than the SQLAlchemy model. Comparing the live result against
    the raw model flag would falsely flag those deliberate choices as
    drift. The guard instead compares against what the bootstrap itself
    declares — so it still catches a column the bootstrap forgot
    entirely (presence) without flagging a known, intentional
    deferred-constraint:

      * `_PENDING_COLUMNS` — each entry's DDL string carries the final
        nullability (`... NOT NULL ...` vs not). The bootstrap is the
        authority for these; the model and the DDL are kept in sync.
      * `organization_id` (`_ORG_ID_PREEXISTING_TABLES`) — added NULLABLE
        on purpose. `app/services/bootstrap.py` documents this: "added
        NULLABLE here; the NOT-NULL flip is Alembic 0007's job and is
        intentionally not part of the bootstrap". So on a database the
        bootstrap alone has upgraded, a nullable `organization_id` is
        CORRECT, not drift — the NOT NULL the model declares is reached
        by the separately-run Alembic 0007.
      * `_PENDING_CONSTRAINTS` — columns the bootstrap adds nullable then
        hardens to NOT NULL itself, after a backfill. The bootstrap IS
        the authority and DOES reach NOT NULL, so these are expected
        NOT NULL.
    """
    from app.services.bootstrap import (
        _ORG_ID_PREEXISTING_TABLES,
        _PENDING_COLUMNS,
        _PENDING_CONSTRAINTS,
    )

    intended: dict[tuple[str, str], bool] = {}
    # _PENDING_COLUMNS: nullability is whatever its DDL declares.
    for table, column, ddl in _PENDING_COLUMNS:
        intended[(table, column)] = "NOT NULL" in ddl.upper()
    # organization_id on pre-existing tables: deliberately nullable —
    # the NOT NULL flip is Alembic 0007's job, not the bootstrap's.
    for table in _ORG_ID_PREEXISTING_TABLES:
        intended[(table, "organization_id")] = False
    # _PENDING_CONSTRAINTS: the bootstrap hardens these to NOT NULL.
    for table, check_query, _ddl in _PENDING_CONSTRAINTS:
        if "column_name = '" in check_query:
            col = check_query.split("column_name = '", 1)[1].split("'", 1)[0]
            intended[(table, col)] = True
    return intended


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


class _AgedDatabase:
    """The result of `_build_aged_database` — what was removed to
    simulate a pre-existing/older production database."""

    def __init__(
        self,
        dropped_columns: list[tuple[str, str]],
        dropped_tables: list[str],
    ) -> None:
        # `(table, column)` pairs dropped from baseline tables.
        self.dropped_columns = dropped_columns
        # Whole org-era tables dropped.
        self.dropped_tables = dropped_tables


def _post_baseline_columns(model_cols: dict) -> set[tuple[str, str]]:
    """Every droppable model column on a baseline table that the Alembic
    0001 baseline does NOT declare — i.e. a column added AFTER the
    squashed baseline.

    On a real upgraded production database the table already exists, so
    `create_all()` never adds these — they reach production only via the
    bootstrap's `ALTER TABLE ADD COLUMN` upgrade path. This is the exact
    v0.6.0 surface, derived purely from the models-vs-Alembic diff.
    """
    out: set[tuple[str, str]] = set()
    for table_name in _BASELINE_TABLES:
        cols = model_cols.get(table_name)
        if cols is None:
            continue
        for col_name, col in cols.items():
            if not _droppable(table_name, col):
                continue
            if (table_name, col_name) not in _BASELINE_COLUMNS:
                out.add((table_name, col_name))
    return out


def _pending_list_columns(model_cols: dict) -> set[tuple[str, str]]:
    """The droppable `(table, column)` pairs the bootstrap's
    `_PENDING_COLUMNS` list claims to manage, restricted to columns that
    still exist on a baseline model table.

    Folded into the drop-set so the guard exercises every column the
    bootstrap's own upgrade list names — the task's "ideally every
    droppable column the upgrade lists claim to manage". This is a
    SUPPLEMENT to `_post_baseline_columns`, never the sole source: the
    Alembic-derived set already covers the v0.6.0 surface independently,
    so gutting `_PENDING_COLUMNS` cannot blind the guard.
    """
    from app.services.bootstrap import _PENDING_COLUMNS

    out: set[tuple[str, str]] = set()
    for table, column, _ddl in _PENDING_COLUMNS:
        cols = model_cols.get(table)
        if (
            table in _BASELINE_TABLES
            and cols is not None
            and column in cols
            and _droppable(table, cols[column])
        ):
            out.add((table, column))
    return out


def _build_aged_database(database_url: str) -> _AgedDatabase:
    """Create the full current schema on `database_url`, then strip it
    back to simulate a PRE-EXISTING, older production database.

    Two complementary kinds of strip, between them reproducing every
    way a real upgraded production database differs from a fresh one:

      * From every BASELINE table (Alembic 0001 — the tables a real
        upgraded DB already HAS) drop every POST-BASELINE column: a
        model column the 0001 migration does not declare, plus every
        column the bootstrap's `_PENDING_COLUMNS` list claims to manage.
        This is the v0.6.0 surface — `create_all()` is a no-op for an
        existing table, so each of these reaches the upgraded DB only if
        the bootstrap's `ALTER TABLE ADD COLUMN` upgrade path knows
        about it. The set is found from the models-vs-Alembic diff, NOT
        from the bootstrap's lists, so an INCOMPLETE upgrade list (the
        actual v0.6.0 bug) is genuinely caught. Baseline-era columns
        (which a real production DB has always had) are deliberately
        NOT dropped — no production database was ever missing those.

      * Drop every ORG-ERA new whole table (a model table not in the
        0001 baseline) ENTIRELY. On a real upgrade these tables did not
        exist; `create_all()` builds them whole. Dropping them here
        proves the bootstrap still creates every new table.

    Primary-key columns are kept — dropping one is meaningless and would
    force a table rebuild. `DROP ... CASCADE` removes any dependent
    index/FK/constraint, faithfully reproducing a database built before
    the column/table existed.

    Returns an `_AgedDatabase` describing exactly what was removed.
    """
    settings = replace(load_settings(), database_url=database_url)
    init_engine(settings)
    engine = get_engine()

    # Start from a genuinely clean database, then build the full,
    # current schema — every table now exists, the precondition for the
    # "table already exists / create_all is a no-op" v0.6.0 surface.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    model_cols = _model_columns()

    column_drop_set = (
        _post_baseline_columns(model_cols)
        | _pending_list_columns(model_cols)
    )
    dropped_columns: list[tuple[str, str]] = sorted(column_drop_set)
    dropped_tables: list[str] = sorted(
        name for name in model_cols if name not in _BASELINE_TABLES
    )

    with engine.begin() as conn:
        for table, column in dropped_columns:
            conn.execute(
                sa.text(
                    f'ALTER TABLE "{table}" '
                    f'DROP COLUMN IF EXISTS "{column}" CASCADE'
                )
            )
        for table in dropped_tables:
            conn.execute(
                sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE')
            )

    return _AgedDatabase(dropped_columns, dropped_tables)


def _live_columns(engine) -> dict[str, dict[str, dict]]:
    """Reflect the live database: `{table: {column: info_dict}}`."""
    inspector = sa.inspect(engine)
    out: dict[str, dict[str, dict]] = {}
    for table in inspector.get_table_names():
        out[table] = {
            c["name"]: c for c in inspector.get_columns(table)
        }
    return out


def test_schema_drift_guard_baseline_parse_is_sane():
    """Sanity: the Alembic-0001 baseline parse yields a real schema.

    The guard's drop-set is derived from the models-vs-baseline diff. If
    the baseline parse silently returned nothing, the guard would drop
    near-everything (or, with a bad table set, nothing) and stop being
    meaningful. This pins the parse: the baseline must name a plausible
    table and column count, the model tables must be a superset of the
    baseline tables, and the diff must surface at least the known
    post-baseline columns (the org-era `organization_id` additions).
    """
    assert len(_BASELINE_TABLES) >= 30, (
        f"Alembic-0001 parse found only {len(_BASELINE_TABLES)} tables "
        "— baseline parse looks broken"
    )
    assert len(_BASELINE_COLUMNS) >= 200, (
        f"Alembic-0001 parse found only {len(_BASELINE_COLUMNS)} columns "
        "— baseline parse looks broken"
    )
    model_tables = set(Base.metadata.tables)
    assert _BASELINE_TABLES <= model_tables, (
        "baseline names tables the models no longer have: "
        f"{sorted(_BASELINE_TABLES - model_tables)}"
    )
    post_baseline = _post_baseline_columns(_model_columns())
    # The org-boundary work added organization_id to pre-existing tenant
    # tables — the canonical post-baseline columns and the v0.6.0
    # incident set. The diff MUST surface them.
    org_id_post = {
        (t, c) for (t, c) in post_baseline if c == "organization_id"
    }
    assert len(org_id_post) >= 8, (
        "the models-vs-baseline diff did not surface the post-baseline "
        f"organization_id columns — got {sorted(org_id_post)}"
    )


def test_bootstrap_upgrades_an_aged_database_to_match_the_models(
    drift_postgres_url,
):
    """THE GUARD.

    Simulate a pre-existing/older production database (full schema, then
    every post-baseline column dropped + every org-era table dropped),
    run the full production bootstrap, and assert the resulting LIVE
    Postgres schema matches the SQLAlchemy models — every model table
    and every model column present.

    This is RED whenever a model column or table exists that the
    bootstrap's idempotent upgrade path does not know to create on an
    already-existing database — i.e. exactly the v0.6.0 bug class.
    """
    from app.services.bootstrap import run_startup_bootstrap

    # ── 1. Build the aged (pre-existing) database. ───────────────────
    aged = _build_aged_database(drift_postgres_url)
    assert aged.dropped_columns, (
        "the guard dropped no columns — it would run near-vacuous; the "
        "models-vs-baseline diff and _PENDING_COLUMNS together name "
        "nothing droppable"
    )
    engine = get_engine()

    # Sanity: the database really is in the aged shape — every dropped
    # column and every dropped table is genuinely absent right now.
    pre = _live_columns(engine)
    for table, column in aged.dropped_columns:
        assert table in pre, f"aged DB missing whole table {table!r}"
        assert column not in pre[table], (
            f"aged DB unexpectedly still has {table}.{column}"
        )
    for table in aged.dropped_tables:
        assert table not in pre, (
            f"aged DB unexpectedly still has org-era table {table!r}"
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
    intended_not_null = _bootstrap_intended_not_null()

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    nullability_drift: list[str] = []

    for table_name, columns in model_cols.items():
        if table_name not in live:
            missing_tables.append(table_name)
            continue
        live_cols = live[table_name]
        for col_name, model_col in columns.items():
            # PRESENCE — the v0.6.0 bug class. Every model column MUST
            # exist on the upgraded database. A column the model declares
            # but the bootstrap never re-adds is exactly the production
            # `column does not exist` incident.
            if col_name not in live_cols:
                missing_columns.append(f"{table_name}.{col_name}")
                continue

            # NULLABILITY — checked only where a single component is the
            # authority for the column's final shape on a
            # bootstrap-only-upgraded database:
            #
            #   * a column the bootstrap's own upgrade path manages
            #     (`_PENDING_COLUMNS` / `_ORG_ID_PREEXISTING_TABLES` /
            #     `_PENDING_CONSTRAINTS`) — compared against the
            #     nullability the bootstrap is DESIGNED to leave it at
            #     (`_bootstrap_intended_not_null`), which is sometimes
            #     deliberately looser than the model (e.g.
            #     `organization_id`, whose NOT NULL flip is Alembic
            #     0007's job, not the bootstrap's — see bootstrap.py).
            #   * any other column — it pre-dates the upgrade or was
            #     built whole by `create_all()` from the model, so it
            #     already carries the model's nullability; compared
            #     against the model flag directly.
            #
            # This catches a column the bootstrap genuinely fails to
            # constrain when it OWNS that step, without flagging a
            # deliberately-deferred NOT NULL as drift.
            live_nullable = live_cols[col_name]["nullable"]
            key = (table_name, col_name)
            if key in intended_not_null:
                expect_not_null = intended_not_null[key]
                authority = "bootstrap upgrade path"
            else:
                expect_not_null = not model_col.nullable
                authority = "model"
            if expect_not_null and live_nullable:
                nullability_drift.append(
                    f"{table_name}.{col_name} "
                    f"(expected NOT NULL per {authority})"
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
            "columns left nullable that should be NOT NULL: "
            f"{sorted(nullability_drift)}"
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

    # ── 4. Everything the guard removed was re-created. ──────────────
    # Explicit, per-item confirmation — makes a failure name the exact
    # column or table the bootstrap forgot, independent of the
    # whole-schema comparison above.
    columns_still_missing = [
        f"{table}.{column}"
        for table, column in aged.dropped_columns
        if column not in live.get(table, {})
    ]
    assert not columns_still_missing, (
        "the bootstrap did not re-create these post-baseline columns on "
        "a pre-existing database (v0.6.0-class drift): "
        f"{sorted(columns_still_missing)}"
    )
    tables_still_missing = [
        table for table in aged.dropped_tables if table not in live
    ]
    assert not tables_still_missing, (
        "the bootstrap did not re-create these org-era tables on a "
        f"pre-existing database: {sorted(tables_still_missing)}"
    )
