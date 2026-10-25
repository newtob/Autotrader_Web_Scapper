"""Scraper wiring: real ``auto_trader.get_total_pages`` into real ``db.save_listings`` (SQLite).

The browser is faked; everything between the HTML and the database is production code.
"""

from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace
from typing import Any

import peewee
import pytest

from tests.conftest import T0, T1
from tests.scraper_fakes import FakeDriver, FakeWait, listing_card_html, search_page_html

POSTCODE = "KT7 0YJ"
ID_A = "202609206214030"
ID_B = "202609206214031"
ID_C = "202609206214032"
ID_P2 = "202609206214099"
EXTRA_OTHER = "Heated Steering"


def _href(ad_id: str) -> str:
    return f"/car-details/{ad_id}?advertising-location=at_cars&sort=relevance"


def _taycan(ad_id: str | None, price: str = "£42,000", title: str = "Porsche Taycan 4S") -> str:
    return listing_card_html(title, _href(ad_id) if ad_id else None, price=price)


@pytest.fixture
def env(schema_db, tmp_path, monkeypatch, sleeps):
    """Wire ``auto_trader`` to a FakeDriver and a wrapped real ``db.save_listings``."""
    import auto_trader
    import db

    extra_name = db.EXTRA_COLUMNS[0]
    clock = {"now": T0}
    error_log = tmp_path / "error_log.txt"
    calls: list[int] = []
    real_save = db.save_listings

    def wrapped_save(rows, **kwargs):
        calls.append(len(rows))
        return real_save(rows, clock=lambda: clock["now"], sleep=sleeps, error_log_path=error_log)

    driver = FakeDriver({1: search_page_html([])})

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_trader, "driver", driver, raising=False)
    monkeypatch.setattr(auto_trader, "WebDriverWait", FakeWait)
    monkeypatch.setattr(auto_trader.time, "sleep", lambda _s: None)
    monkeypatch.setattr(auto_trader, "scroll_to_load_all", lambda *a, **k: 0)
    monkeypatch.setattr(auto_trader, "EXTRA_COLUMNS", list(db.EXTRA_COLUMNS), raising=False)
    monkeypatch.setattr(auto_trader, "EXTRAS_PRICES", dict(db.EXTRAS_PRICES), raising=False)
    monkeypatch.setattr(auto_trader, "get_extras", lambda *a, **k: ({extra_name: 100}, EXTRA_OTHER))
    # Wiring must go through the module attribute (``db.save_listings``), never a from-import.
    monkeypatch.setattr(db, "save_listings", wrapped_save)

    def scrape(**overrides: Any) -> None:
        kwargs: dict[str, Any] = {
            "postcode": POSTCODE,
            "make": "Porsche",
            "model": "Taycan",
            "trim": "4S",
            "fuel": "Electric",
            "year_from": "2020",
            "year_to": "2026",
            "radius": "50",
            "price_to": "",
            "page_start": 1,
        }
        kwargs.update(overrides)
        auto_trader.get_total_pages(**kwargs)

    return SimpleNamespace(
        db=db,
        driver=driver,
        calls=calls,
        clock=clock,
        sleeps=sleeps,
        error_log=error_log,
        extra_name=extra_name,
        extra_col=db.EXTRA_COLUMN_MAP[extra_name],
        scrape=scrape,
        test_db=schema_db,
    )


def _by_key(env_, ad_id: str):
    return env_.db.Listing.get(env_.db.Listing.ad_key == ad_id)


def _count(env_) -> int:
    return env_.db.Listing.select().count()


def test_get_total_pages_saves_page_rows_via_save_listings(env) -> None:
    """W1: cards on one page reach SQLite through the real save path with injected fields."""
    env.driver.pages = {
        1: search_page_html(
            [
                _taycan(ID_A),
                _taycan(ID_B, price="£50,000"),
                listing_card_html("BMW i4 M50", _href(ID_C)),
            ]
        )
    }

    env.scrape()

    assert env.calls == [2]  # exactly one save_listings call, BMW filtered out
    assert _count(env) == 2
    row = _by_key(env, ID_A)
    assert row.make == "Porsche"
    assert row.model == "Taycan"
    assert row.model_type == "4S"
    assert row.postcode == POSTCODE
    assert row.detail_url == f"https://www.autotrader.co.uk/car-details/{ID_A}"
    assert row.price_gbp == 42000
    assert row.year == 2021
    assert row.miles == 12345
    assert row.fuel_type == "Electric"
    assert getattr(row, env.extra_col) == 100
    assert row.extras_total == 100
    assert row.extras_other == EXTRA_OTHER
    assert _by_key(env, ID_B).price_gbp == 50000


def test_second_scrape_run_resights_via_get_total_pages(env) -> None:
    """W2: a second run at T1 upserts A, leaves B alone and inserts C."""
    env.driver.pages = {1: search_page_html([_taycan(ID_A), _taycan(ID_B, price="£50,000")])}
    env.clock["now"] = T0
    env.scrape()
    assert _count(env) == 2

    env.driver.pages = {1: search_page_html([_taycan(ID_A, price="£40,500"), _taycan(ID_C, price="£61,000")])}
    env.clock["now"] = T1
    env.scrape()

    assert _count(env) == 3
    a = _by_key(env, ID_A)
    assert a.first_found == T0
    assert a.last_found == T1
    assert a.price_gbp == 40500
    assert a.days_live == (T1 - T0).days
    b = _by_key(env, ID_B)
    assert b.last_found == T0
    assert b.price_gbp == 50000
    c = _by_key(env, ID_C)
    assert c.first_found == T1
    assert c.last_found == T1
    assert c.days_live == 0


def test_db_outage_on_one_page_does_not_stop_next_page(env) -> None:
    """W3: page 1 exhausts its DB retries; page 2 is still scraped and saved."""
    env.driver.pages = {
        1: search_page_html([_taycan(ID_A)], total_pages=2, current=1),
        2: search_page_html([_taycan(ID_P2, price="£55,000")], total_pages=2, current=2),
    }
    env.test_db.fail_on(r"^\s*(SELECT|INSERT|UPDATE)", times=env.db.MAX_DB_ATTEMPTS)

    env.scrape()  # must not raise

    assert env.db.Listing.get_or_none(env.db.Listing.ad_key == ID_A) is None
    assert _by_key(env, ID_P2).price_gbp == 55000
    assert _count(env) == 1
    assert env.sleeps == [1.0, 2.0, 4.0]
    assert env.error_log.exists()
    assert len(env.error_log.read_text().splitlines()) >= 1
    assert env.calls == [1, 1]


def test_non_retryable_db_error_handled_by_page_handler(env, caplog) -> None:
    """W4: a non-retryable error is caught by the page-level handler; page 2 still saves."""
    env.driver.pages = {
        1: search_page_html([_taycan(ID_A)], total_pages=2, current=1),
        2: search_page_html([_taycan(ID_P2)], total_pages=2, current=2),
    }
    env.test_db.fail_on(r"^\s*INSERT", times=1, exc=peewee.IntegrityError)

    with caplog.at_level(logging.DEBUG):
        env.scrape()  # must not raise

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]
    assert errors, "page handler must log the exception via log.exception"
    page_log = env.error_log.parent / "error_log.txt"  # handler uses the default path (cwd = tmp_path)
    assert page_log.exists()
    assert page_log.read_text().strip() != ""
    assert env.sleeps == []
    assert env.db.Listing.get_or_none(env.db.Listing.ad_key == ID_A) is None
    assert _by_key(env, ID_P2).price_gbp == 42000


def test_card_without_href_is_saved_with_fallback_key(env, caplog) -> None:
    """W5: a card with no href is saved under a nokey_ fallback and a WARNING."""
    env.driver.pages = {1: search_page_html([_taycan(None)])}

    with caplog.at_level(logging.DEBUG):
        env.scrape()

    assert _count(env) == 1
    row = env.db.Listing.get()
    assert row.ad_key.startswith("nokey_")
    assert row.detail_url is None
    assert any(r.levelno == logging.WARNING and "nokey_" in r.getMessage() for r in caplog.records)


def test_get_total_pages_signature() -> None:
    """W6: ``output_file`` is gone from the signature."""
    import auto_trader

    params = list(inspect.signature(auto_trader.get_total_pages).parameters)
    assert params == [
        "postcode",
        "make",
        "model",
        "trim",
        "fuel",
        "year_from",
        "year_to",
        "radius",
        "price_to",
        "page_start",
    ]
