"""Component tests for the ``db.save_listings`` retry loop (coverage rows R1-R6)."""

from __future__ import annotations

import logging

import peewee
import pytest

import db
from tests.conftest import T0

AD_ID = "202609206214030"
URL_B = "https://www.autotrader.co.uk/car-details/202609206214031"
ANY_QUERY = r"^\s*(SELECT|INSERT|UPDATE)"
EXPECTED_SLEEPS = [1.0, 2.0, 4.0]


def _count() -> int:
    return db.Listing.select().count()


@pytest.mark.parametrize("exc", [peewee.OperationalError, peewee.InterfaceError])
def test_retry_then_succeed(schema_db, make_row, sleeps, tmp_path, caplog, monkeypatch, exc) -> None:
    original_close = schema_db.close
    close_calls: list[int] = []

    def spy_close(*args, **kwargs):
        close_calls.append(1)
        return original_close(*args, **kwargs)

    monkeypatch.setattr(schema_db, "close", spy_close)
    schema_db.fail_on(ANY_QUERY, times=db.MAX_DB_ATTEMPTS - 1, exc=exc)

    with caplog.at_level(logging.DEBUG):
        result = db.save_listings([make_row()], clock=lambda: T0, sleep=sleeps, error_log_path=tmp_path / "error_log.txt")

    assert result.succeeded
    assert result.attempts == db.MAX_DB_ATTEMPTS
    assert _count() == 1
    assert sleeps == EXPECTED_SLEEPS
    assert len(close_calls) == db.MAX_DB_ATTEMPTS - 1
    assert any(r.levelno == logging.INFO and "retry_count=3" in r.getMessage() for r in caplog.records)


def test_retry_exhausted_logs_error_and_continues(schema_db, make_row, sleeps, tmp_path, caplog) -> None:
    error_log = tmp_path / "error_log.txt"
    schema_db.fail_on(ANY_QUERY, times=10_000)

    with caplog.at_level(logging.DEBUG):
        result = db.save_listings([make_row()], clock=lambda: T0, sleep=sleeps, error_log_path=error_log)

    assert result == db.SaveResult(False, db.MAX_DB_ATTEMPTS, 0, 0)
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)
    lines = error_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "OperationalError" in lines[0]
    assert sleeps == EXPECTED_SLEEPS

    schema_db.clear_faults()
    again = db.save_listings([make_row()], clock=lambda: T0, sleep=sleeps, error_log_path=error_log)
    assert again.succeeded
    assert again.saved == 1
    assert _count() == 1


def test_exhausted_mid_batch_leaves_no_partial_rows(schema_db, make_row, sleeps, tmp_path) -> None:
    schema_db.fail_on(r"^\s*INSERT", times=db.MAX_DB_ATTEMPTS, skip=1)
    rows = [make_row(), make_row(detail_url=URL_B)]

    result = db.save_listings(rows, clock=lambda: T0, sleep=sleeps, error_log_path=tmp_path / "error_log.txt")

    assert result.succeeded is False
    assert result.saved == 0
    assert _count() == 0


def test_non_retryable_error_propagates(schema_db, make_row, sleeps, tmp_path) -> None:
    schema_db.fail_on(r"^\s*INSERT", times=1, exc=peewee.IntegrityError)

    with pytest.raises(peewee.IntegrityError):
        db.save_listings([make_row()], clock=lambda: T0, sleep=sleeps, error_log_path=tmp_path / "error_log.txt")

    assert sleeps == []


def test_failed_reconnect_counts_as_attempt(schema_db, make_row, sleeps, tmp_path, monkeypatch) -> None:
    original_connect = schema_db.connect
    connect_calls: list[int] = []

    def flaky_connect(*args, **kwargs):
        connect_calls.append(1)
        if len(connect_calls) == 1:
            raise peewee.OperationalError("injected reconnect failure")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(schema_db, "connect", flaky_connect)
    # attempt 1 fails on its first query; the reconnect at the start of attempt 2 then fails
    schema_db.fail_on(ANY_QUERY, times=1)

    result = db.save_listings([make_row()], clock=lambda: T0, sleep=sleeps, error_log_path=tmp_path / "error_log.txt")

    assert result.succeeded
    assert result.attempts == 3
    assert sleeps == [1.0, 2.0]
    assert _count() == 1


def test_first_attempt_success_has_no_retry_log(schema_db, make_row, sleeps, tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        result = db.save_listings([make_row()], clock=lambda: T0, sleep=sleeps, error_log_path=tmp_path / "error_log.txt")

    assert result.attempts == 1
    assert sleeps == []
    assert not any("retry_count=" in r.getMessage() for r in caplog.records)
