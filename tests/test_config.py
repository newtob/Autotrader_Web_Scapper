"""Acceptance tests for DATABASE_URL configuration and fail-fast behaviour (rows C1-C8).

Scenario 5 (missing config fails fast), plus scenario 12 for the schema-error path of C3.
"""

from __future__ import annotations

import logging
import sqlite3
import traceback
from pathlib import Path
from typing import Any

import peewee
import pytest

import db

SENTINEL = "SENTINEL-s3cret"


def _no_op(*_args: Any, **_kwargs: Any) -> None:
    return None


# --------------------------------------------------------------------------- C1


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "whitespace"])
def test_missing_database_url_fails_fast(value: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    import db

    env: dict[str, str] = {} if value is None else {"DATABASE_URL": value}

    with pytest.raises(db.ConfigError) as via_mapping:
        db.get_database_url(env)
    assert "DATABASE_URL" in str(via_mapping.value)
    assert ".env.template" in str(via_mapping.value)

    # Default source is os.environ.
    if value is not None:
        monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(db.ConfigError) as via_environ:
        db.get_database_url()
    assert "DATABASE_URL" in str(via_environ.value)
    assert ".env.template" in str(via_environ.value)

    with pytest.raises(db.ConfigError) as via_init:
        db.init_db()
    assert "DATABASE_URL" in str(via_init.value)
    assert ".env.template" in str(via_init.value)


# --------------------------------------------------------------------------- C2


@pytest.mark.parametrize(
    "bad_url",
    [f"mysql://u:{SENTINEL}@h/d", SENTINEL],
    ids=["wrong-scheme", "bare-string"],
)
def test_invalid_database_url_does_not_leak_value(bad_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import db

    caplog.set_level(logging.DEBUG)
    caught: list[BaseException] = []

    with pytest.raises(db.ConfigError) as exc_info:
        db.get_database_url({"DATABASE_URL": bad_url})
    caught.append(exc_info.value)

    with pytest.raises(db.ConfigError) as exc_info:
        db.connect_from_env({"DATABASE_URL": bad_url})
    caught.append(exc_info.value)

    monkeypatch.setenv("DATABASE_URL", bad_url)
    with pytest.raises(db.ConfigError) as exc_info:
        db.init_db()
    caught.append(exc_info.value)

    for exc in caught:
        assert "DATABASE_URL" in str(exc)
        assert ".env.template" in str(exc)
        assert SENTINEL not in str(exc)
        assert SENTINEL not in repr(exc)
        full_traceback = "".join(traceback.format_exception(exc))
        assert SENTINEL not in full_traceback

    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
    assert SENTINEL not in caplog.text


# --------------------------------------------------------------------------- C3


@pytest.mark.parametrize("case", ["unset", "empty-sqlite"])
def test_main_fails_before_launching_browser(case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auto_trader
    import db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_trader, "load_dotenv", _no_op)

    def _fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("webdriver.Firefox must not be constructed before init_db succeeds")

    monkeypatch.setattr(auto_trader.webdriver, "Firefox", _fail_if_called)

    empty_db = tmp_path / "empty.db"
    if case == "unset":
        expected: type[Exception] = db.ConfigError
    else:
        expected = db.SchemaError
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{empty_db}")

    try:
        with pytest.raises(expected) as exc_info:
            auto_trader.main()
    finally:
        db.close_db()

    if case == "empty-sqlite":
        assert "just db-init" in str(exc_info.value)
        if empty_db.exists():
            conn = sqlite3.connect(str(empty_db))
            try:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            finally:
                conn.close()
            assert "listing" not in tables
    else:
        assert "DATABASE_URL" in str(exc_info.value)


# --------------------------------------------------------------------------- C4


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@h/d", "postgresql://u:p@h:6543/d?sslmode=require", "sqlite:///x.db"],
)
def test_accepted_url_schemes(url: str) -> None:
    import db

    assert db.get_database_url({"DATABASE_URL": f"  {url}\t\n"}) == url
    assert db.get_database_url({"DATABASE_URL": url}) == url


# --------------------------------------------------------------------------- C5


def test_connect_from_env_postgres_is_lazy_and_never_logs_url(caplog: pytest.LogCaptureFixture) -> None:
    import db

    caplog.set_level(logging.DEBUG)
    url = f"postgresql://postgres.ref:{SENTINEL}@h.pooler.supabase.com:6543/postgres?sslmode=require"

    database = db.connect_from_env({"DATABASE_URL": url})

    assert isinstance(database, peewee.PostgresqlDatabase)
    assert database.is_closed()
    assert SENTINEL not in caplog.text
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()


# --------------------------------------------------------------------------- C6


def test_db_module_does_not_load_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import db

    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///x.db\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(db.ConfigError):
        db.connect_from_env()


# --------------------------------------------------------------------------- C7


def test_db_init_run_returns_1_on_config_error(caplog: pytest.LogCaptureFixture) -> None:
    import db_init

    caplog.set_level(logging.DEBUG)

    assert db_init.run() == 1
    assert "DATABASE_URL" in caplog.text


# --------------------------------------------------------------------------- C8


def test_main_order_and_cleanup_on_error(schema_db: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auto_trader
    import db

    db_path = schema_db.database
    schema_db.close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(auto_trader, "load_dotenv", _no_op)
    monkeypatch.setattr(auto_trader, "_configure_logging", _no_op)
    monkeypatch.setattr(auto_trader, "_load_extras_prices", _no_op)

    events: list[str] = []

    real_init_db = db.init_db

    def recording_init_db(*args: Any, **kwargs: Any) -> Any:
        events.append("init_db")
        return real_init_db(*args, **kwargs)

    monkeypatch.setattr(db, "init_db", recording_init_db)

    class FakeFirefox:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            events.append("Firefox")

        def maximize_window(self) -> None:
            pass

        def quit(self) -> None:
            events.append("quit")

    monkeypatch.setattr(auto_trader.webdriver, "Firefox", FakeFirefox)
    monkeypatch.setattr(auto_trader, "setup_cookies", lambda: events.append("setup_cookies"))

    def failing_get_config(*_args: Any, **_kwargs: Any) -> None:
        events.append("get_config")
        raise RuntimeError("boom")

    monkeypatch.setattr(auto_trader, "get_config", failing_get_config)

    with pytest.raises(RuntimeError, match="boom"):
        auto_trader.main()

    assert events == ["init_db", "Firefox", "setup_cookies", "get_config", "quit"]
    assert db.database.is_closed()


# --------------------------------------------------------------------------- C9 (sslmode)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_postgres_url_defaults_sslmode_to_require(scheme: str) -> None:
    import db

    url = db.with_default_sslmode(f"{scheme}://u:p@h:6543/d")
    assert url == f"{scheme}://u:p@h:6543/d?sslmode=require"

    with_query = db.with_default_sslmode(f"{scheme}://u:p@h/d?application_name=x")
    assert with_query == f"{scheme}://u:p@h/d?application_name=x&sslmode=require"


def test_explicit_sslmode_and_sqlite_are_left_alone() -> None:
    import db

    explicit = "postgresql://u:p@h/d?sslmode=verify-full"
    assert db.with_default_sslmode(explicit) == explicit
    assert db.with_default_sslmode("sqlite:///x.db") == "sqlite:///x.db"


def test_connect_from_env_passes_sslmode_require_to_postgres() -> None:
    import db

    database = db.connect_from_env({"DATABASE_URL": f"postgresql://u:{SENTINEL}@h:6543/d"})

    assert isinstance(database, peewee.PostgresqlDatabase)
    assert database.connect_params["sslmode"] == "require"

    explicit = db.connect_from_env({"DATABASE_URL": "postgresql://u:p@h:6543/d?sslmode=disable"})
    assert explicit.connect_params["sslmode"] == "disable"


def test_connect_from_env_percent_decodes_user_and_password() -> None:
    """A ``%21`` in the URL must reach Postgres as ``!`` (playhouse does not decode by default)."""
    database = db.connect_from_env({"DATABASE_URL": "postgresql://postgres.ref:p%21ss%40w%25rd@h.pooler.supabase.com:6543/postgres"})
    assert database.connect_params["password"] == "p!ss@w%rd"
    assert database.connect_params["user"] == "postgres.ref"
    literal = db.connect_from_env({"DATABASE_URL": "postgresql://postgres.ref:pa!ss@h.pooler.supabase.com:6543/postgres"})
    assert literal.connect_params["password"] == "pa!ss"


# --------------------------------------------------------------------------- C10: unreachable database


_UNREACHABLE_URL = "postgresql://postgres.ref:Sup3r%21secret@127.0.0.1:1/postgres"  # port 1: connection refused, no network needed


def test_init_db_raises_clean_error_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    import db

    monkeypatch.setenv("DATABASE_URL", _UNREACHABLE_URL)
    with pytest.raises(db.DatabaseUnavailableError) as exc_info:
        db.init_db()
    message = str(exc_info.value)
    assert "could not connect" in message
    assert "host='127.0.0.1'" in message
    assert "Sup3r" not in message
    assert exc_info.value.__cause__ is None  # raw driver error (which may echo the target) is not chained
    db.close_db()


def test_cli_logs_unreachable_database_without_traceback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import auto_trader
    import db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_trader, "load_dotenv", _no_op)
    monkeypatch.setenv("DATABASE_URL", _UNREACHABLE_URL)

    def _fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("webdriver.Firefox must not be constructed when the database is unreachable")

    monkeypatch.setattr(auto_trader.webdriver, "Firefox", _fail_if_called)
    with caplog.at_level(logging.ERROR):
        code = auto_trader.cli()
    assert code == 1
    assert "could not connect" in caplog.text
    assert "Sup3r" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    assert db.database.is_closed()


@pytest.mark.parametrize("case", ["config", "schema"])
def test_cli_returns_1_for_config_and_schema_errors(case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auto_trader

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auto_trader, "load_dotenv", _no_op)
    if case == "schema":
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    assert auto_trader.cli() == 1
