"""Backfill ``daily_stats`` from existing listings (idempotent; safe to re-run).

Run with ``just db-backfill``. Requires ``DATABASE_URL`` and a schema from ``just db-init``.
"""

from __future__ import annotations

import logging

import peewee
from dotenv import load_dotenv

import db
import stats

logger = logging.getLogger("autotrader.backfill")


def run(database: peewee.Database | None = None) -> int:
    """Backfill every captured day and return a process exit code (``0`` ok, ``1`` error)."""
    stage = "configure"
    try:
        if database is None:
            database = db.connect_from_env()
        db.bind_database(database)
        stage = "connect"
        database.connect(reuse_if_open=True)
        db.check_schema()
        rows = stats.backfill_daily_stats()
        result = db.save_daily_stats(rows)
        if not result.succeeded:
            logger.error("Backfill failed after %d attempts; see error_log.txt", result.attempts)
            return 1
    except (db.ConfigError, db.SchemaError) as exc:
        logger.error("%s", exc)
        return 1
    except peewee.DatabaseError as exc:
        for line in db.describe_database_failure(exc, database, connecting=stage == "connect"):
            logger.error("%s", line)
        return 1
    finally:
        if database is not None and not database.is_closed():
            database.close()
    days = len({r["stat_date"] for r in rows})
    logger.info("Backfilled %d row(s) across %d day(s)", result.saved, days)
    return 0


def main() -> None:
    """Entrypoint for ``just db-backfill``."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    raise SystemExit(run())


if __name__ == "__main__":
    main()
