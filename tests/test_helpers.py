"""Unit tests for the pure helpers in ``db`` (coverage rows H1-H8)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import pytest

import db

AD_ID = "202609206214030"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Panoramic Roof Fixed Glass", "panoramic_roof_fixed_glass"),
        ("BOSE Surround Sound System", "bose_surround_sound_system"),
        ("20-inch Taycan Turbo S Aero Design Wheels", "extra_20_inch_taycan_turbo_s_aero_design_wheels"),
        ("4+1 Seating System Rear 2+1 Bench", "extra_4_1_seating_system_rear_2_1_bench"),
        ("150kW DC On-Board Booster", "extra_150kw_dc_on_board_booster"),
        # runs of punctuation and whitespace collapse to a single underscore
        ("Heated -- Seats ,, (Front)", "heated_seats_front"),
        # leading and trailing separators are stripped
        ("  --Sport Chrono--  ", "sport_chrono"),
    ],
)
def test_snake_case_samples(name: str, expected: str) -> None:
    assert db.snake_case(name) == expected


def _expected_extra_names() -> list[str]:
    """Ordered union of extra names across all segments, computed from the real JSON."""
    data = json.loads(Path(db.EXTRAS_PRICES_PATH).read_text(encoding="utf-8"))
    names: list[str] = []
    for segment in data.values():
        for extra in segment:
            if extra not in names:
                names.append(extra)
    return names


def test_extra_column_map_covers_all_extras() -> None:
    expected_names = _expected_extra_names()
    assert len(expected_names) > 0
    assert len(db.EXTRA_COLUMN_MAP) == len(expected_names)
    assert len(db.EXTRA_COLUMN_MAP) == len(db.EXTRA_COLUMNS)
    assert list(db.EXTRA_COLUMN_MAP) == list(db.EXTRA_COLUMNS)
    assert list(db.EXTRA_COLUMN_MAP) == expected_names

    columns = list(db.EXTRA_COLUMN_MAP.values())
    assert len(set(columns)) == len(columns)
    for column in columns:
        assert re.fullmatch(r"^[a-z_][a-z0-9_]*$", column), column
    assert set(columns).isdisjoint(db.FIXED_COLUMNS)


def test_build_extra_column_map_rejects_collision() -> None:
    with pytest.raises(ValueError):
        db.build_extra_column_map(["Heated Seats", "heated-seats"])
    # collides with the fixed column `title`
    with pytest.raises(ValueError):
        db.build_extra_column_map(["Title"])


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.autotrader.co.uk/car-details/{AD_ID}", AD_ID),
        (f"/car-details/{AD_ID}", AD_ID),
        (f"https://www.autotrader.co.uk/car-details/{AD_ID}?advertising-location=at_cars&sort=relevance", AD_ID),
        (f"https://www.autotrader.co.uk/car-details/{AD_ID}/", AD_ID),
        (f"https://www.autotrader.co.uk/car-details/{AD_ID}#gallery", AD_ID),
        (None, None),
        ("", None),
        ("https://www.autotrader.co.uk/car-details/20260920621403", None),  # 14 digits
        ("https://www.autotrader.co.uk/car-details/2026092062140301", None),  # 16 digits
        (f"https://www.autotrader.co.uk/car-details/{AD_ID}/photos", None),  # last segment rule
    ],
)
def test_extract_ad_key(url: str | None, expected: str | None) -> None:
    assert db.extract_ad_key(url) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("£42,000", 42000), ("42000", 42000), (" £ 42,000 ", 42000), ("POA", None), ("", None), (None, None)],
)
def test_parse_price(text: str | None, expected: int | None) -> None:
    assert db.parse_price_gbp(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2021 (21 reg)", 2021), ("NA", None), ("21 reg", None)],
)
def test_parse_year(text: str | None, expected: int | None) -> None:
    assert db.parse_year(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12,345 miles", 12345), ("1 mile", 1), ("NA", None), (None, None)],
)
def test_parse_miles(text: str | None, expected: int | None) -> None:
    assert db.parse_miles(text) == expected


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 30.0), (7, 30.0)],
)
def test_backoff_seconds(attempt: int, expected: float) -> None:
    assert db.backoff_seconds(attempt) == expected


def test_fallback_ad_key_is_deterministic() -> None:
    key = db.fallback_ad_key("Porsche Taycan", 2021, 12345, "Electric")
    assert key == db.fallback_ad_key("Porsche Taycan", 2021, 12345, "Electric")
    assert re.fullmatch(r"nokey_[0-9a-f]{40}", key)
    assert key == "nokey_" + hashlib.sha1(b"Porsche Taycan|2021|12345|Electric").hexdigest()

    assert db.fallback_ad_key("t", None, None, None) == db.fallback_ad_key("t", None, None, "")
    assert db.fallback_ad_key(None, None, None, None) == db.fallback_ad_key("", None, None, None)
    assert db.fallback_ad_key("Porsche Taycan", 2021, 12346, "Electric") != key


def test_load_extras_prices_missing_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        result = db.load_extras_prices(tmp_path / "does_not_exist.json")
    assert result == ({}, [])
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_column_names_fit_postgres_identifier_limit() -> None:
    """Postgres truncates identifiers over 63 chars, so every derived column must already fit."""
    long_name = "Adaptive Sports Seats in Front (18-way, electric) with Memory Package"
    assert len(db.snake_case(long_name)) <= db.MAX_IDENTIFIER_LENGTH
    assert not db.snake_case(long_name).endswith("_")
    assert all(len(column) <= db.MAX_IDENTIFIER_LENGTH for column in db.EXTRA_COLUMN_MAP.values())


def test_truncation_collisions_are_rejected() -> None:
    prefix = "A" * 70
    with pytest.raises(ValueError, match="both map to column"):
        db.build_extra_column_map([prefix + " one", prefix + " two"])
