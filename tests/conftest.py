"""Shared fixtures for the 0002 Database and ORM acceptance tests.

Production modules ``db`` and ``db_init`` are imported lazily inside fixtures so that this
file stays importable before they exist.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import peewee
import pytest

# Fixed, timezone-aware UTC instants used as injected clocks.
T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=3, hours=5)

# Transaction-control statements are never fault targets.
_TXN_CONTROL = re.compile(r"^\s*(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b", re.IGNORECASE)


class InstrumentedSqliteDatabase(peewee.SqliteDatabase):
    """SQLite database that records executed SQL and can inject faults."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.executed: list[str] = []
        self._faults: list[dict[str, Any]] = []

    def fail_on(
        self,
        pattern: str,
        times: int,
        skip: int = 0,
        exc: type[Exception] = peewee.OperationalError,
    ) -> None:
        """Arm a fault.

        After skipping the first ``skip`` statements matching the regex ``pattern``, the next
        ``times`` matching statements raise ``exc``. BEGIN, COMMIT, ROLLBACK and SAVEPOINT
        statements never match.

        Args:
            pattern: Regex searched against the SQL string.
            times: Number of matching statements that raise.
            skip: Number of matching statements to let through first.
            exc: Exception class to raise.
        """
        self._faults.append({"re": re.compile(pattern), "times": times, "skip": skip, "exc": exc})

    def clear_faults(self) -> None:
        """Disarm all faults."""
        self._faults.clear()

    def execute_sql(self, sql: str, params: Any = None, *args: Any, **kwargs: Any) -> Any:
        """Record the statement, raise any armed fault, then delegate."""
        self.executed.append(sql)
        if not _TXN_CONTROL.match(sql):
            for fault in self._faults:
                if not fault["re"].search(sql):
                    continue
                if fault["skip"] > 0:
                    fault["skip"] -= 1
                    continue
                if fault["times"] > 0:
                    fault["times"] -= 1
                    raise fault["exc"](f"injected fault on: {sql[:60]}")
        return super().execute_sql(sql, params, *args, **kwargs)


class SleepRecorder(list):
    """A list of recorded sleep durations that is also callable as ``sleep=``."""

    def __call__(self, seconds: float) -> None:
        self.append(seconds)

    record = __call__


@pytest.fixture(autouse=True)
def _no_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no test ever sees a real DATABASE_URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def test_db(tmp_path: Path):
    """File-backed SQLite database under tmp_path (D2); closed on teardown."""
    database = InstrumentedSqliteDatabase(str(tmp_path / "test.db"))
    try:
        yield database
    finally:
        import db

        try:
            db.close_db()
        finally:
            if not database.is_closed():
                database.close()


@pytest.fixture
def schema_db(test_db: InstrumentedSqliteDatabase) -> InstrumentedSqliteDatabase:
    """``test_db`` with the full schema built by ``db_init.run`` and bound via ``init_db``."""
    import db
    import db_init

    assert db_init.run(test_db) == 0
    db.init_db(test_db)
    return test_db


@pytest.fixture
def make_row() -> Callable[..., dict[str, Any]]:
    """Factory returning a valid ``ListingRow`` dict; keyword overrides replace fields."""

    def _make(**overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "title": "Porsche Taycan 4S",
            "price": "42000",
            "year": "2021 (21 reg)",
            "miles": "12,345 miles",
            "fuel_type": "Electric",
            "detail_url": "https://www.autotrader.co.uk/car-details/202609206214030",
            "make": "Porsche",
            "model": "Taycan",
            "model_type": "4S",
            "postcode": "KT7 0YJ",
            "extras": {},
            "extras_other": "",
        }
        row.update(overrides)
        return row

    return _make


@pytest.fixture
def sleeps() -> SleepRecorder:
    """Sleep recorder: compare to a list, and pass it as ``sleep=``."""
    return SleepRecorder()


@pytest.fixture
def t0() -> datetime:
    return T0


@pytest.fixture
def t1() -> datetime:
    return T1
