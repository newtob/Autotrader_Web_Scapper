"""Acceptance tests for ``db_init``: schema creation, idempotence, additive sync, atomicity."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import peewee
import pytest

import db
import db_init
from tests.conftest import T0, InstrumentedSqliteDatabase

_DDL = re.compile(r"^\s*(CREATE|ALTER|DROP)\b", re.IGNORECASE)


def _ddl(statements: list[str]) -> list[str]:
    return [s for s in statements if _DDL.match(s)]


def _column_names(database: peewee.Database) -> list[str]:
    return [c.name for c in database.get_columns(db.TABLE_NAME)]


def _extra_cols() -> list[str]:
    return list(db.EXTRA_COLUMN_MAP.values())


def _save_rows(make_row, tmp_path: Path, extras_cols: dict[int, int] | None = None) -> None:
    """Save two rows; ``extras_cols`` maps EXTRA_COLUMNS index to a non-zero price."""
    extras = {db.EXTRA_COLUMNS[i]: price for i, price in (extras_cols or {}).items()}
    rows = [
        make_row(extras=extras),
        make_row(
            detail_url="https://www.autotrader.co.uk/car-details/202609206214031",
            title="Porsche Taycan Turbo",
            price="60000",
            extras=extras,
        ),
    ]
    result = db.save_listings(rows, clock=lambda: T0, sleep=lambda _s: None, error_log_path=tmp_path / "error_log.txt")
    assert result.succeeded
    assert result.saved == 2


def _snapshot(database: peewee.Database, columns: list[str]) -> list[tuple]:
    quoted = ", ".join(f'"{c}"' for c in columns)
    return database.execute_sql(f'SELECT {quoted} FROM "listing" ORDER BY id').fetchall()  # noqa: S608


def test_db_init_creates_full_schema(test_db: InstrumentedSqliteDatabase, tmp_path: Path) -> None:
    assert db_init.run(test_db) == 0

    expected = list(db.FIXED_COLUMNS) + _extra_cols()
    assert _column_names(test_db) == expected

    info = {row[1]: row for row in test_db.execute_sql('PRAGMA table_info("listing")').fetchall()}
    for col in _extra_cols():
        _cid, _name, _type, notnull, default, _pk = info[col]
        assert notnull == 1, col
        assert str(default) == "0", col
    assert info["first_found"][2].upper() == "TIMESTAMPTZ"

    # ad_key is unique: a duplicate insert must violate the constraint.
    fields = {
        "ad_key": "202609206214099",
        "title": "t",
        "price_gbp": 1,
        "make": "Porsche",
        "postcode": "KT7 0YJ",
        "first_found": T0,
        "last_found": T0,
    }
    db.Listing.create(**fields)
    with pytest.raises(peewee.IntegrityError):
        db.Listing.create(**fields)

    # sync_schema on a brand-new database reports the table as created.
    fresh = InstrumentedSqliteDatabase(str(tmp_path / "fresh.db"))
    try:
        db.bind_database(fresh)
        fresh.connect(reuse_if_open=True)
        report = db_init.sync_schema(fresh)
        assert report.created_table
        assert _column_names(fresh) == expected
    finally:
        if not fresh.is_closed():
            fresh.close()


def test_db_init_is_idempotent(schema_db: InstrumentedSqliteDatabase, make_row, tmp_path: Path) -> None:
    _save_rows(make_row, tmp_path, {0: 100})
    columns_before = _column_names(schema_db)
    rows_before = _snapshot(schema_db, columns_before)
    assert len(rows_before) == 2
    mark = len(schema_db.executed)

    assert db_init.run(schema_db) == 0

    assert _ddl(schema_db.executed[mark:]) == []
    schema_db.connect(reuse_if_open=True)
    assert _column_names(schema_db) == columns_before
    assert _snapshot(schema_db, columns_before) == rows_before

    schema_db.connect(reuse_if_open=True)
    db.bind_database(schema_db)
    report = db_init.sync_schema(schema_db)
    assert report.added_columns == []
    assert not report.created_table


def test_db_init_adds_only_missing_extra_column(schema_db: InstrumentedSqliteDatabase, make_row, tmp_path: Path) -> None:
    extras = _extra_cols()
    dropped, kept = extras[1], extras[2]
    _save_rows(make_row, tmp_path, {1: 50, 2: 75})
    schema_db.execute_sql(f'ALTER TABLE listing DROP COLUMN "{dropped}"')

    others = [c for c in _column_names(schema_db) if c != dropped]
    before = _snapshot(schema_db, others)
    kept_idx = others.index(kept)
    assert all(r[kept_idx] == 75 for r in before)

    schema_db.connect(reuse_if_open=True)
    db.bind_database(schema_db)
    report = db_init.sync_schema(schema_db)
    assert report.added_columns == [dropped]
    assert not report.created_table

    assert dropped in _column_names(schema_db)
    assert schema_db.execute_sql(f'SELECT "{dropped}" FROM listing').fetchall() == [(0,), (0,)]
    assert _snapshot(schema_db, others) == before

    again = db_init.sync_schema(schema_db)
    assert again.added_columns == []


def test_db_init_rolls_back_on_failure_after_first_alter(schema_db: InstrumentedSqliteDatabase) -> None:
    extras = _extra_cols()
    first, second = extras[1], extras[2]
    schema_db.execute_sql(f'ALTER TABLE listing DROP COLUMN "{first}"')
    schema_db.execute_sql(f'ALTER TABLE listing DROP COLUMN "{second}"')
    before = set(_column_names(schema_db))
    assert first not in before
    assert second not in before

    schema_db.fail_on(r"^\s*ALTER TABLE", times=1, skip=1)
    assert db_init.run(schema_db) != 0

    schema_db.clear_faults()
    schema_db.connect(reuse_if_open=True)
    assert set(_column_names(schema_db)) == before

    assert db_init.run(schema_db) == 0
    schema_db.connect(reuse_if_open=True)
    after = set(_column_names(schema_db))
    assert first in after
    assert second in after


def test_db_init_warns_on_orphan_column_and_keeps_it(
    schema_db: InstrumentedSqliteDatabase, make_row, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _save_rows(make_row, tmp_path)
    schema_db.execute_sql("ALTER TABLE listing ADD COLUMN legacy_extra INTEGER")
    schema_db.execute_sql("UPDATE listing SET legacy_extra = 7")

    with caplog.at_level(logging.WARNING):
        assert db_init.run(schema_db) == 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "legacy_extra" in r.getMessage()]
    assert warnings

    schema_db.connect(reuse_if_open=True)
    assert "legacy_extra" in _column_names(schema_db)
    assert schema_db.execute_sql("SELECT legacy_extra FROM listing").fetchall() == [(7,), (7,)]

    db.bind_database(schema_db)
    report = db_init.sync_schema(schema_db)
    assert report.orphan_columns == ["legacy_extra"]


def test_db_init_skips_advisory_lock_on_sqlite(test_db: InstrumentedSqliteDatabase) -> None:
    assert db_init.run(test_db) == 0
    assert not [s for s in test_db.executed if "pg_advisory" in s]


def test_db_init_fails_on_missing_fixed_column(test_db: InstrumentedSqliteDatabase, caplog: pytest.LogCaptureFixture) -> None:
    cols = [c for c in db.FIXED_COLUMNS if c != "postcode"]
    defs = ['"id" INTEGER PRIMARY KEY'] + [f'"{c}" TEXT' for c in cols if c != "id"]
    defs += [f'"{c}" INTEGER NOT NULL DEFAULT 0' for c in _extra_cols()]
    test_db.connect(reuse_if_open=True)
    test_db.execute_sql(f"CREATE TABLE listing ({', '.join(defs)})")
    mark = len(test_db.executed)

    with caplog.at_level(logging.DEBUG):
        assert db_init.run(test_db) == 1

    assert "postcode" in caplog.text
    assert not [s for s in test_db.executed[mark:] if re.match(r"^\s*ALTER\b", s, re.IGNORECASE)]


class _UnreachablePostgres(peewee.SqliteDatabase):
    """Stands in for a Postgres handle whose connect fails with a secret-bearing message."""

    def __init__(self, message: str) -> None:
        super().__init__(":memory:")
        self.connect_params = {"host": "db.example.invalid", "port": 5432, "user": "postgres", "password": "S3cret!pw", "sslmode": "require"}
        self._message = message

    def connect(self, reuse_if_open: bool = False) -> bool:
        raise peewee.OperationalError(self._message)


@pytest.mark.parametrize(
    ("message", "hint_fragment"),
    [
        ('could not translate host name "db.example.invalid" to address', "IPv6-only"),
        ("FATAL:  password authentication failed for user S3cret!pw", "percent-encode"),
        ("FATAL:  Tenant or user not found", "postgres.<project-ref>"),
    ],
)
def test_db_init_connect_failure_reports_cause_target_and_hint_without_secrets(
    message: str, hint_fragment: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:S3cret!pw@db.example.invalid:5432/postgres")
    with caplog.at_level(logging.ERROR):
        code = db_init.run(_UnreachablePostgres(message))
    assert code == 1
    assert "could not connect" in caplog.text
    assert "OperationalError" in caplog.text
    assert "host='db.example.invalid'" in caplog.text
    assert "user='postgres'" in caplog.text
    assert hint_fragment in caplog.text
    assert "S3cret!pw" not in caplog.text
    assert "rolled back" not in caplog.text
