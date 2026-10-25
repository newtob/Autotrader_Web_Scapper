"""Acceptance tests: the scraper's ``init_db`` is read-only and never issues DDL."""

from __future__ import annotations

import re

import pytest

import db
from tests.conftest import InstrumentedSqliteDatabase

_DDL = re.compile(r"^\s*(CREATE|ALTER|DROP)\b", re.IGNORECASE)


def _ddl(statements: list[str]) -> list[str]:
    return [s for s in statements if _DDL.match(s)]


def test_init_db_fails_when_table_missing(test_db: InstrumentedSqliteDatabase) -> None:
    with pytest.raises(db.SchemaError) as excinfo:
        db.init_db(test_db)

    assert "`just db-init`" in str(excinfo.value)
    assert _ddl(test_db.executed) == []
    assert not test_db.table_exists(db.TABLE_NAME)


def test_init_db_fails_when_extra_column_missing(schema_db: InstrumentedSqliteDatabase) -> None:
    dropped = list(db.EXTRA_COLUMN_MAP.values())[1]
    schema_db.execute_sql(f'ALTER TABLE listing DROP COLUMN "{dropped}"')
    mark = len(schema_db.executed)

    with pytest.raises(db.SchemaError) as excinfo:
        db.init_db(schema_db)

    message = str(excinfo.value)
    assert dropped in message
    assert "`just db-init`" in message
    assert _ddl(schema_db.executed[mark:]) == []
    assert dropped not in [c.name for c in schema_db.get_columns(db.TABLE_NAME)]


def test_init_db_allows_orphan_column(schema_db: InstrumentedSqliteDatabase) -> None:
    schema_db.execute_sql("ALTER TABLE listing ADD COLUMN legacy_extra INTEGER")
    db.init_db(schema_db)


def test_init_db_fails_when_fixed_column_missing(test_db: InstrumentedSqliteDatabase) -> None:
    cols = [c for c in db.FIXED_COLUMNS if c != "last_found"]
    defs = ['"id" INTEGER PRIMARY KEY'] + [f'"{c}" TEXT' for c in cols if c != "id"]
    defs += [f'"{c}" INTEGER NOT NULL DEFAULT 0' for c in db.EXTRA_COLUMN_MAP.values()]
    test_db.connect(reuse_if_open=True)
    test_db.execute_sql(f"CREATE TABLE listing ({', '.join(defs)})")

    with pytest.raises(db.SchemaError) as excinfo:
        db.init_db(test_db)

    assert "last_found" in str(excinfo.value)
