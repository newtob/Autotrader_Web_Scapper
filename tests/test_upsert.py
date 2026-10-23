"""Component tests for ``db.save_listings`` insert/update behaviour (coverage rows U1-U9)."""

from __future__ import annotations

import logging
from datetime import timedelta, timezone

import pytest

import db
from tests.conftest import T0, T1

AD_ID = "202609206214030"
URL_A = f"https://www.autotrader.co.uk/car-details/{AD_ID}"
URL_B = "https://www.autotrader.co.uk/car-details/202609206214031"


def _price_of(name: str) -> int:
    for segment in db.EXTRAS_PRICES.values():
        if name in segment:
            return segment[name]
    raise AssertionError(name)


def _col(name: str) -> str:
    return db.EXTRA_COLUMN_MAP[name]


def _get(ad_key: str):
    return db.Listing.get(db.Listing.ad_key == ad_key)


def _count() -> int:
    return db.Listing.select().count()


def test_insert_on_first_sight(schema_db, make_row, tmp_path) -> None:
    a, b = db.EXTRA_COLUMNS[0], db.EXTRA_COLUMNS[1]
    others = db.EXTRA_COLUMNS[2:]
    row = make_row(model="", extras={a: _price_of(a), b: _price_of(b)}, extras_other="")

    result = db.save_listings([row], clock=lambda: T0, error_log_path=tmp_path / "error_log.txt")

    assert result == db.SaveResult(True, 1, 1, 0)
    assert _count() == 1
    listing = _get(AD_ID)
    assert listing.first_found == T0
    assert listing.last_found == T0
    assert listing.first_found.utcoffset() == timedelta(0)
    assert listing.days_live == 0
    assert listing.price_gbp == 42000
    assert getattr(listing, _col(a)) == _price_of(a)
    assert getattr(listing, _col(b)) == _price_of(b)
    for name in others:
        assert getattr(listing, _col(name)) == 0, name
    assert listing.extras_total == _price_of(a) + _price_of(b)
    assert listing.model is None
    assert listing.extras_other is None


def test_resight_updates_price_and_preserves_first_found(schema_db, make_row, tmp_path) -> None:
    a, b = db.EXTRA_COLUMNS[0], db.EXTRA_COLUMNS[1]
    log = tmp_path / "error_log.txt"
    db.save_listings([make_row(extras={a: _price_of(a)})], clock=lambda: T0, error_log_path=log)

    result = db.save_listings(
        [make_row(price="45000", postcode="SW1A 1AA", extras={b: _price_of(b)})],
        clock=lambda: T1,
        error_log_path=log,
    )

    assert result.succeeded
    assert _count() == 1
    listing = _get(AD_ID)
    assert listing.price_gbp == 45000
    assert listing.first_found == T0
    assert listing.last_found == T1
    assert listing.days_live == 3  # 3 days 5 hours: floor of whole days
    assert listing.postcode == "SW1A 1AA"
    assert getattr(listing, _col(a)) == 0
    assert getattr(listing, _col(b)) == _price_of(b)
    assert listing.extras_total == _price_of(b)


@pytest.mark.parametrize(
    "detail_url",
    [
        None,
        "https://www.autotrader.co.uk/car-details/not-an-id",
        "https://www.autotrader.co.uk/car-details/20260920621403",  # 14 digits
        f"https://www.autotrader.co.uk/car-details/{AD_ID}/photos",
    ],
)
def test_ad_key_fallback_when_url_has_no_id(schema_db, make_row, tmp_path, caplog, detail_url) -> None:
    row = make_row(detail_url=detail_url)
    expected = db.fallback_ad_key(row["title"], 2021, 12345, "Electric")
    log = tmp_path / "error_log.txt"

    with caplog.at_level(logging.DEBUG):
        db.save_listings([row], clock=lambda: T0, error_log_path=log)

    listing = db.Listing.get()
    assert listing.ad_key.startswith("nokey_")
    assert listing.ad_key == expected
    assert any(r.levelno == logging.WARNING and "nokey_" in r.getMessage() for r in caplog.records)

    db.save_listings([row], clock=lambda: T1, error_log_path=log)
    assert _count() == 1


def test_rows_without_title_or_unparseable_price_are_skipped(schema_db, make_row, tmp_path, caplog) -> None:
    rows = [make_row(), make_row(title=None, detail_url=URL_B), make_row(price="POA", detail_url=URL_B)]

    with caplog.at_level(logging.DEBUG):
        result = db.save_listings(rows, clock=lambda: T0, error_log_path=tmp_path / "error_log.txt")

    assert result.saved == 1
    assert result.skipped == 2
    assert _count() == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


def test_unknown_extra_name_is_ignored_with_warning(schema_db, make_row, tmp_path, caplog) -> None:
    row = make_row(extras={"Not A Real Extra": 999, db.EXTRA_COLUMNS[0]: 100})

    with caplog.at_level(logging.DEBUG):
        db.save_listings([row], clock=lambda: T0, error_log_path=tmp_path / "error_log.txt")

    listing = _get(AD_ID)
    assert listing.extras_total == 100
    assert getattr(listing, _col(db.EXTRA_COLUMNS[0])) == 100
    assert any(r.levelno == logging.WARNING and "Not A Real Extra" in r.getMessage() for r in caplog.records)


def test_timestamps_round_trip_as_utc(schema_db, make_row, tmp_path) -> None:
    plus_one = T0.astimezone(timezone(timedelta(hours=1)))
    assert plus_one.utcoffset() == timedelta(hours=1)

    db.save_listings([make_row()], clock=lambda: plus_one, error_log_path=tmp_path / "error_log.txt")

    listing = _get(AD_ID)
    assert listing.first_found == T0
    assert listing.last_found == T0
    assert listing.first_found.utcoffset() == timedelta(0)
    assert listing.last_found.utcoffset() == timedelta(0)


def test_mixed_batch_new_and_existing(schema_db, make_row, tmp_path) -> None:
    log = tmp_path / "error_log.txt"
    db.save_listings([make_row(detail_url=URL_A)], clock=lambda: T0, error_log_path=log)

    result = db.save_listings([make_row(detail_url=URL_A), make_row(detail_url=URL_B)], clock=lambda: T1, error_log_path=log)

    assert result.saved == 2
    assert _count() == 2
    assert _get(AD_ID).first_found == T0
    assert _get("202609206214031").first_found == T1


def test_empty_rows_is_noop(schema_db, tmp_path) -> None:
    before = list(schema_db.executed)

    result = db.save_listings([], clock=lambda: T0, error_log_path=tmp_path / "error_log.txt")

    assert result == db.SaveResult(True, 0, 0, 0)
    assert schema_db.executed == before


def test_na_values_stored_as_null(schema_db, make_row, tmp_path) -> None:
    row = make_row(year="NA", miles="NA", fuel_type="NA")

    result = db.save_listings([row], clock=lambda: T0, error_log_path=tmp_path / "error_log.txt")

    assert result.saved == 1
    listing = _get(AD_ID)
    assert listing.year is None
    assert listing.miles is None
    assert listing.fuel_type is None
