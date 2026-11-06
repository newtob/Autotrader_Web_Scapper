"""Acceptance tests for ``daily_stats``: compute, upsert idempotence, backfill, schema provisioning."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import peewee
import pytest

import backfill_stats
import db
import db_init
import stats
from tests.conftest import T0, T1, InstrumentedSqliteDatabase

TAYCAN = ("Porsche", "Taycan", "4S")
SEARCHES = [TAYCAN, ("Tesla", "Model 3", "")]


def _save(make_row, tmp_path: Path, when, *prices: int, start: int = 0, **overrides) -> None:
    rows = [
        make_row(price=str(p), detail_url=f"https://www.autotrader.co.uk/car-details/{202609206214000 + start + i}", **overrides)
        for i, p in enumerate(prices)
    ]
    assert db.save_listings(rows, clock=lambda: when, sleep=lambda _s: None, error_log_path=tmp_path / "e.txt").succeeded


def _stored() -> list[tuple]:
    q = db.DailyStat.select().order_by(db.DailyStat.stat_date, db.DailyStat.make)
    return [(r.stat_date, r.make, r.model, r.model_type, r.car_count, r.avg_price_gbp, r.avg_days_live) for r in q]


def test_compute_averages_and_zero_car_rows(schema_db, make_row, tmp_path: Path) -> None:
    _save(make_row, tmp_path, T0, 40000, 43001)

    rows = stats.compute_daily_stats(T0.date(), searches=SEARCHES)

    by_make = {r["make"]: r for r in rows}
    assert by_make["Porsche"]["car_count"] == 2
    assert by_make["Porsche"]["avg_price_gbp"] == 41501  # 41500.5 rounds half up
    assert by_make["Porsche"]["avg_days_live"] == Decimal("0.0")
    assert by_make["Tesla"]["car_count"] == 0
    assert by_make["Tesla"]["avg_price_gbp"] is None
    assert by_make["Tesla"]["avg_days_live"] is None


def test_compute_only_counts_cars_seen_that_utc_day(schema_db, make_row, tmp_path: Path) -> None:
    _save(make_row, tmp_path, T0, 40000)
    rows = stats.compute_daily_stats(date(2026, 9, 21), searches=[TAYCAN])
    assert rows[0]["car_count"] == 0


def test_compute_skips_failed_search_but_keeps_other_rows(schema_db, make_row, tmp_path: Path) -> None:
    rows = stats.compute_daily_stats(T0.date(), searches=SEARCHES, skip={TAYCAN})
    assert [r["make"] for r in rows] == ["Tesla"]


def test_save_daily_stats_is_idempotent_and_overwrites(schema_db, make_row, tmp_path: Path) -> None:
    _save(make_row, tmp_path, T0, 40000)
    rows = stats.compute_daily_stats(T0.date(), searches=[TAYCAN])
    assert db.save_daily_stats(rows, clock=lambda: T0).saved == 1
    first = _stored()
    assert db.save_daily_stats(rows, clock=lambda: T1).succeeded
    assert _stored() == first

    _save(make_row, tmp_path, T0, 50000, start=10)
    db.save_daily_stats(stats.compute_daily_stats(T0.date(), searches=[TAYCAN]), clock=lambda: T1)
    assert len(_stored()) == 1
    assert _stored()[0][4:6] == (2, 45000)


def test_backfill_covers_every_captured_day_and_is_rerunnable(schema_db, make_row, tmp_path: Path) -> None:
    _save(make_row, tmp_path, T0, 40000)  # first seen day 1
    _save(make_row, tmp_path, T1, 44000)  # same ad re-seen 3 days later, price now 44000
    assert backfill_stats.run(schema_db) == 0
    schema_db.connect(reuse_if_open=True)

    rows = _stored()
    assert [r[0] for r in rows] == [date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    assert all(r[4] == 1 and r[5] == 44000 for r in rows)
    assert [r[6] for r in rows] == [Decimal(n) for n in ("0.0", "1.0", "2.0", "3.0")]

    assert backfill_stats.run(schema_db) == 0
    schema_db.connect(reuse_if_open=True)
    assert _stored() == rows


def test_db_init_creates_daily_stats_once(test_db: InstrumentedSqliteDatabase) -> None:
    assert db_init.run(test_db) == 0
    assert test_db.table_exists(db.DAILY_TABLE_NAME)
    test_db.executed.clear()
    assert db_init.run(test_db) == 0
    assert not [s for s in test_db.executed if s.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))]


def test_unique_key_rejects_duplicate_rows(schema_db) -> None:
    fields = {"stat_date": T0.date(), "make": "A", "model": "", "model_type": "", "car_count": 0, "computed_at": T0}
    db.DailyStat.create(**fields)
    with pytest.raises(peewee.IntegrityError):
        db.DailyStat.create(**fields)


def test_check_schema_requires_daily_stats(schema_db) -> None:
    schema_db.drop_tables([db.DailyStat])
    with pytest.raises(db.SchemaError, match="daily_stats"):
        db.check_schema()


def test_scraper_hook_writes_stats_and_never_raises(schema_db, make_row, tmp_path: Path, monkeypatch) -> None:
    import auto_trader

    _save(make_row, tmp_path, T0, 40000)
    monkeypatch.setattr(stats, "load_searches", lambda *a, **k: [TAYCAN])
    monkeypatch.setattr(auto_trader, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: T0)}))
    auto_trader._save_daily_stats()
    assert [r[4] for r in _stored()] == [1]

    monkeypatch.setattr(stats, "compute_daily_stats", lambda *a, **k: 1 / 0)
    auto_trader._save_daily_stats()  # must swallow the failure
