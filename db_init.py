"""Create and sync the ``listing`` schema (local, idempotent, additive-only).

Run with ``just db-init``. Requires ``DATABASE_URL`` (see ``.env.template``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import peewee
from dotenv import load_dotenv

import db

logger = logging.getLogger("autotrader.db_init")

DB_INIT_LOCK_KEY = 726_150_920_240_001
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class SyncReport:
    """What ``sync_schema`` changed or noticed."""

    created_table: bool
    added_columns: list[str]
    orphan_columns: list[str]


def sync_schema(database: peewee.Database) -> SyncReport:
    """Bring the live ``listing`` table in line with the model, in one transaction.

    Creates the table only when absent. Adds missing extra columns as
    ``INTEGER NOT NULL DEFAULT 0``. Never drops, renames or retypes anything.

    Args:
        database: The database to sync. The model proxy is bound to it if it is not already.

    Returns:
        A ``SyncReport``.

    Raises:
        db.SchemaError: If a fixed column is missing from an existing table (manual migration).
    """
    if db.database.obj is not database:
        db.bind_database(database)

    created = False
    added: list[str] = []
    with database.atomic():
        if isinstance(database, peewee.PostgresqlDatabase):
            database.execute_sql("SELECT pg_advisory_xact_lock(%s)", (DB_INIT_LOCK_KEY,))

        if not database.table_exists(db.TABLE_NAME):
            database.create_tables([db.Listing], safe=True)
            created = True

        live = [c.name for c in database.get_columns(db.TABLE_NAME)]
        live_set = set(live)

        missing_fixed = [c for c in db.FIXED_COLUMNS if c not in live_set]
        if missing_fixed:
            raise db.SchemaError(
                f"Table {db.TABLE_NAME!r} is missing fixed column(s): {', '.join(missing_fixed)}. "
                "This needs a manual migration; `just db-init` only adds extra columns."
            )

        for column in db.EXTRA_COLUMN_MAP.values():
            if column in live_set:
                continue
            if not _IDENTIFIER_RE.match(column):
                raise ValueError(f"Refusing to use unsafe column identifier {column!r}")
            database.execute_sql(f'ALTER TABLE "{db.TABLE_NAME}" ADD COLUMN "{column}" INTEGER NOT NULL DEFAULT 0')
            added.append(column)

        expected = set(db.FIXED_COLUMNS) | set(db.EXTRA_COLUMN_MAP.values())
        orphans = [c for c in live if c not in expected]

    for column in orphans:
        logger.warning("Orphan column %r is in the database but not in the model; leaving it alone", column)
    return SyncReport(created, added, orphans)


def run(database: peewee.Database | None = None) -> int:
    """Provision the schema and return a process exit code.

    Args:
        database: Database to use; defaults to ``db.connect_from_env()``.

    Returns:
        ``0`` on success (including a no-op), ``1`` on a config, schema or database error.
    """
    stage = "configure"
    try:
        if database is None:
            database = db.connect_from_env()
        db.bind_database(database)
        stage = "connect"
        database.connect(reuse_if_open=True)
        stage = "sync"
        report = sync_schema(database)
    except db.ConfigError as exc:
        logger.error("%s", exc)
        return 1
    except db.SchemaError as exc:
        logger.error("Schema sync failed: %s", exc)
        return 1
    except peewee.DatabaseError as exc:
        lines = db.describe_database_failure(exc, database, connecting=stage == "connect")
        for line in lines:
            logger.error("%s", line)
        return 1
    finally:
        if database is not None and not database.is_closed():
            database.close()

    if report.created_table:
        logger.info("Created table %r with %d column(s)", db.TABLE_NAME, len(db.FIXED_COLUMNS) + len(db.EXTRA_COLUMN_MAP))
    if report.added_columns:
        logger.info("Added %d column(s): %s", len(report.added_columns), ", ".join(f"added {c}" for c in report.added_columns))
    if not report.created_table and not report.added_columns:
        logger.info("Schema already up to date: no changes")
    return 0


def main() -> None:
    """Entrypoint for ``just db-init``."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    raise SystemExit(run())


if __name__ == "__main__":
    main()
