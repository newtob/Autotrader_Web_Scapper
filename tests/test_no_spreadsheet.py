"""Acceptance tests for the removal of the spreadsheet output (rows N1-N4).

Scenario 6 (no spreadsheet produced).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- N1


def test_import_does_not_load_pandas_or_openpyxl(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    code = (
        "import auto_trader, db, db_init, sys; loaded = [m for m in ('pandas', 'openpyxl') if m in sys.modules]; print('LOADED=' + ','.join(loaded))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "LOADED=", result.stdout
    assert not (tmp_path / "scraper.log").exists()


# --------------------------------------------------------------------------- N2


def test_get_config_run_writes_db_not_xlsx(
    schema_db: Any,
    make_row: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import auto_trader
    import db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_trader.time, "sleep", lambda _s: None)
    config = tmp_path / "autotrader_config.csv"
    config.write_text("PostalCode,PageNumber,year-from,year-to,radius\nKT70YJ,1,2020,2022,100\n", encoding="utf-8")

    monkeypatch.setattr(auto_trader, "get_make_data", lambda: [("Porsche", "Taycan", "Electric", "4S", "")])

    received: list[dict[str, Any]] = []

    def fake_get_total_pages(
        postcode: str,
        make: str,
        model: str,
        trim: str,
        fuel: str,
        year_from: str,
        year_to: str,
        radius: str,
        price_to: str,
        page_start: int = 1,
    ) -> None:
        received.append({"postcode": postcode, "make": make, "page_start": page_start})
        db.save_listings([make_row()])

    monkeypatch.setattr(auto_trader, "get_total_pages", fake_get_total_pages)

    with caplog.at_level(logging.INFO):
        auto_trader.get_config(str(config))

    assert list(tmp_path.glob("*.xlsx")) == []
    assert db.Listing.select().count() == 1
    assert len(received) == 1
    assert received[0]["page_start"] == 1
    assert isinstance(received[0]["page_start"], int)
    assert "Scrape complete. 1 listing(s) in database" in caplog.text
    assert not hasattr(auto_trader, "save_data")
    assert not hasattr(auto_trader, "get_file_name")


# --------------------------------------------------------------------------- N3


def _dep_names(specs: list[str]) -> set[str]:
    names = set()
    for spec in specs:
        match = re.match(r"[A-Za-z0-9_.\-]+", spec.strip())
        assert match, spec
        names.add(match.group(0).lower().replace("_", "-"))
    return names


def test_dependencies_drop_pandas_and_openpyxl() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    deps = _dep_names(data["project"]["dependencies"])
    dev = _dep_names(data["dependency-groups"]["dev"])

    assert "pandas" not in deps
    assert "openpyxl" not in deps
    assert {"peewee", "psycopg2-binary", "python-dotenv"} <= deps
    assert "pytest" in dev


# --------------------------------------------------------------------------- N4

_CSV_TEXT_LF = "PostalCode,PageNumber,year-from,year-to,radius\nKT70YJ,1,2020,2022,100\nSW1A1AA,3,2018,2021,50\n"


@pytest.mark.parametrize("variant", ["lf", "crlf-bom"])
def test_read_config_with_stdlib_csv(variant: str, tmp_path: Path) -> None:
    import auto_trader

    path = tmp_path / "config.csv"
    if variant == "lf":
        path.write_bytes(_CSV_TEXT_LF.encode("utf-8"))
    else:
        path.write_bytes(b"\xef\xbb\xbf" + _CSV_TEXT_LF.replace("\n", "\r\n").encode("utf-8"))

    configs = auto_trader.read_config(str(path))

    expected = [
        auto_trader.SearchConfig(postcode="KT70YJ", page_number=1, year_from="2020", year_to="2022", radius="100"),
        auto_trader.SearchConfig(postcode="SW1A1AA", page_number=3, year_from="2018", year_to="2021", radius="50"),
    ]
    assert configs == expected
    assert all(isinstance(c.page_number, int) for c in configs)
    for c in configs:
        assert all(isinstance(v, str) for v in (c.postcode, c.year_from, c.year_to, c.radius))


# --------------------------------------------------------------------------- N5 (summary count resilience)


def _run_get_config_with_failing_count(test_db: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, times: int) -> None:
    import auto_trader

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_trader.time, "sleep", lambda _s: None)
    config = tmp_path / "autotrader_config.csv"
    config.write_text("PostalCode,PageNumber,year-from,year-to,radius\nKT70YJ,1,2020,2022,100\n", encoding="utf-8")
    monkeypatch.setattr(auto_trader, "get_make_data", lambda: [("Porsche", "Taycan", "Electric", "4S", "")])
    monkeypatch.setattr(auto_trader, "get_total_pages", lambda *a, **k: None)
    test_db.fail_on(r"^\s*SELECT COUNT", times=times)
    auto_trader.get_config(str(config))


def test_summary_count_failure_logs_warning_and_finishes(
    schema_db: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        _run_get_config_with_failing_count(schema_db, monkeypatch, tmp_path, times=1000)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("listing" in r.getMessage().lower() and "count" in r.getMessage().lower() for r in warnings)


def test_summary_count_reconnects_after_dropped_connection(
    schema_db: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        _run_get_config_with_failing_count(schema_db, monkeypatch, tmp_path, times=1)

    assert "Scrape complete. 0 listing(s) in database" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
