# Plan 0002: Database and ORM

Design: [`0002-Database-and-ORM.md`](0002-Database-and-ORM.md) (treated as `design.md`).
Status at plan time: **nothing implemented**. There is no `db.py`, `db_init.py`, `tests/`,
`TECH_DEBT.md` or `.env.template` yet. `STATE.md` shows 0002 as "spec to be written".

This plan is markdown only. Do not commit. The human commits.

> **A1 verification note (implementer).** The Context7 MCP tools were not available to the
> implementing agent, so the APIs were verified empirically against the installed versions
> instead (peewee 4.5.3, psycopg2-binary 2.9.13, python-dotenv 1.2.4). Results:
>
> - `playhouse.db_url.connect(url, unquote_password=False, unquote_user=False, **connect_params)`
>   supports `postgres`, `postgresql` and `sqlite`; `autoconnect=False` is honoured; URL query
>   parameters (`sslmode=require`) land in `connect_params` and reach psycopg2. Confirmed, no deviation.
> - `DatabaseProxy` forwards attribute access to the wrapped database once `initialize()` has been called.
> - `get_columns()` returns `ColumnMetadata` objects with `.name`. Confirmed.
> - `python-dotenv` `load_dotenv(override=False)` keeps an existing **empty** `DATABASE_URL`. Confirmed.
> - **Not verifiable offline (still needs the human Supabase UAT in `verification.md` Part 3):** the
>   Supabase pooler URI shape (host `*.pooler.supabase.com`, port `6543`, user `postgres.<ref>`), and
>   psycopg2 behaviour on the transaction pooler. psycopg2 does not use server-side prepared
>   statements by default, so none is expected to be a problem.
> - No deviations from the plan were found.

---

## 0. Decisions that resolve ambiguity in the design

These are binding for the test-writer and the implementer. Each one either fills a gap in the
design or adjusts a detail so the design's own acceptance scenarios can be tested
deterministically.

| #   | Topic | Decision | Why |
| --- | ----- | -------- | --- |
| D1  | Database handle | `db.database` is a `peewee.DatabaseProxy()`. Nothing connects at import time. `bind_database(db)` calls `database.initialize(db)`. Production builds the real `PostgresqlDatabase` from `DATABASE_URL` using `playhouse.db_url.connect(url, autoconnect=False)`. | If the module connected at import, importing `db` (in tests or tooling) would fail without `DATABASE_URL`. A proxy lets tests inject SQLite without needing `Model.bind` on each test. This still matches the design's intent ("module-level `database`", tests swap the handle). |
| D2  | Test database | Tests use a **file-backed SQLite database under pytest `tmp_path`**, not `:memory:`. | The retry loop has to `close()` and reconnect. With in-memory SQLite, closing the connection destroys the schema and all data, so scenarios 3 and 4 could not be tested. A tmp_path file still needs no network and no Supabase. |
| D3  | Where `load_dotenv()` runs | Only in the entrypoints, `auto_trader.main()` and `db_init.main()`, using `from dotenv import load_dotenv` at module level so tests can monkeypatch it. It never runs at `db` import time and never inside `init_db()`. | Otherwise a developer's real `.env` would leak into tests and make scenario 5 flaky. |
| D4  | Fail fast before the browser | `auto_trader.main()` calls `load_dotenv()` and then `db.init_db()` **before** it creates `webdriver.Firefox`. `get_config()` assumes the database is already initialised and does not call `init_db()`. | A missing URL or a stale schema should not open Firefox first. This differs from the design's data-flow sketch, which puts `init_db` under `get_config`. The behaviour is the same, just earlier. |
| D5  | Accepted `DATABASE_URL` schemes | `postgres://`, `postgresql://` (Supabase), and `sqlite:///` (local development and the local UAT steps only). Any other scheme, or an empty or whitespace-only value, raises `ConfigError`. The error message names `DATABASE_URL` and `.env.template` and **never includes the value**. | The SQLite scheme lets `verification.md` include runnable local steps without Supabase credentials. The invalid-value check follows the [[env_file_usage]] "missing **and invalid**" rule. |
| D6  | Timestamps | `first_found` and `last_found` use a small custom field, `UtcDateTimeField(DateTimeField)`, with `field_type = "TIMESTAMPTZ"`. `db_value` takes a timezone-aware datetime and stores its UTC ISO-8601 string (Postgres casts that to `timestamptz`). `python_value` accepts a `datetime` (psycopg2) or a `str` (SQLite) and always returns a **timezone-aware UTC** `datetime`. | Peewee's stock `DateTimeField` maps to `timestamp` without a time zone, and it cannot round-trip timezone-aware strings on SQLite. This field keeps the design's `timestamptz` and behaves the same on both backends. |
| D7  | String columns | `TextField` for every string column, including `ad_key` (`TextField(unique=True)`). | The design's Postgres column type is `text`. Peewee's `CharField` would create `varchar(255)` instead. |
| D8  | Model construction | A static `ListingBase(BaseModel)` holds all fixed fields. `Listing: type[ListingBase] = type("Listing", (ListingBase,), {**extra_fields, "Meta": ...})` adds the dynamic extra columns, with `Meta.table_name = "listing"`. | `ty` can then resolve the fixed fields statically. Only the dynamic extra columns need `getattr` or targeted ignores, which follows the design's "targeted, not blanket" rule. `ListingBase` is never created as a table. |
| D9  | Extra-column collisions | `build_extra_column_map()` raises `ValueError` if two canonical keys slugify to the same column name, or if an extra's slug collides with a fixed column name. | The design records collision handling as TECH_DEBT ("not de-duplicated"). Failing loudly at import is cheap and prevents a silent dropped column. It still does not de-duplicate. |
| D10 | Where extras JSON loading lives | `db.load_extras_prices(path=EXTRAS_PRICES_PATH) -> tuple[dict[str, dict[str, int]], list[str]]` is the one loader. `db.py` calls it at import to build `EXTRA_COLUMN_MAP` and `Listing`. `auto_trader._load_extras_prices()` delegates to it. A missing file logs a WARNING and returns `({}, [])`, the same as today. | `db.py` cannot import `auto_trader` (circular import, plus Selenium and logging side effects). This removes the duplicated loader. |
| D11 | Logging setup | Move handler setup in `auto_trader.py` (the console handler and the `scraper.log` FileHandler) into `_configure_logging()`, which `main()` calls. `db.py` and `db_init.py` use `logging.getLogger("autotrader.db")` and `logging.getLogger("autotrader.db_init")`. Both propagate. | Importing `auto_trader` in tests must not create `scraper.log` in the repo. Child loggers propagate, so `caplog` captures their records. |
| D12 | Row shape | `auto_trader` builds rows with **snake_case keys** (`ListingRow`, see the contract). There is no `Title`/`Price` mapping layer. `save_listings` parses and normalises the values. | This is simpler, and it is one of the two options the design allows. |
| D13 | Missing fixed column in a live table | `db_init` only auto-adds **extra** columns, as `integer NOT NULL DEFAULT 0`. If the live table lacks a *fixed* column, `db_init` exits non-zero and names the column for manual migration. This is recorded in TECH_DEBT. | Fixed `NOT NULL` columns have no safe default. The table is created complete on first run, so this only happens after manual drift. |
| D14 | `db_init` does not re-run CREATE on an existing table | `sync_schema` checks `db.table_exists("listing")` first and calls `create_tables([Listing], safe=True)` only when the table is absent. | Scenario 9 can then assert that **no** DDL runs on an up-to-date database. Peewee's `safe=True` would otherwise still send `CREATE ... IF NOT EXISTS`. |
| D15 | How columns are added | `db_init` issues `ALTER TABLE "listing" ADD COLUMN "<col>" INTEGER NOT NULL DEFAULT 0` via `db.execute_sql`. Identifiers are quoted with the database's quoting and validated against `^[a-z_][a-z0-9_]*$` before use. `playhouse.migrate` is not used. | `SqliteMigrator.add_column` for `NOT NULL` columns rebuilds the table. That is many statements and would hide the "after the first ALTER" fault point. Plain `ADD COLUMN ... NOT NULL DEFAULT 0` works on both backends. |
| D16 | Retry granularity and sleeps | Each attempt runs the whole page batch in one `database.atomic()`. Sleeps happen **between** attempts only, so at most `MAX_DB_ATTEMPTS - 1` sleeps: `1.0, 2.0, 4.0`. Reconnect (`close()`, then `connect(reuse_if_open=True)`) runs at the start of each retry attempt, inside the guarded block, so a failed reconnect counts as that attempt failing. | This makes the behaviour deterministic and the sleep sequence assertable. |
| D17 | Error-log path | One helper, `db.append_error_log(message, path=ERROR_LOG_PATH)`, with module constant `ERROR_LOG_PATH = Path("error_log.txt")`. `save_listings` accepts `error_log_path=` for tests. `auto_trader`'s page-level handler reuses the helper. | This removes the duplicated `open("error_log.txt", "a")`, and tests can redirect the path to `tmp_path`. |
| D18 | `uat` mode | The design has no `uat:` frontmatter. The caller asked for `verification.md`, so this phase is treated as **`uat: guide`**. The implementer adds `uat: guide` frontmatter to the design file. | The verifier agent reads that key. |
| D19 | Local SQLite artefacts | Add `*.db` and `*.sqlite3` to `.gitignore`. | The local UAT steps create `verify.db`. |
| D20 | Ruff line length | Use `line-length = 150` as in `pyproject.toml` (AGENTS.md says 100, which is stale). Fix AGENTS.md in the docs step. | `pyproject.toml` is the source of truth. |

---

## 1. Test contract

The test-designer and test-writer code against this. Names and signatures are binding.

### 1.1 `db.py` (repo root, top-level module `db`)

Constants:

| Name | Type and value |
| ---- | -------------- |
| `MAX_DB_ATTEMPTS` | `int = 4` |
| `BASE_BACKOFF_SECONDS` | `float = 1.0` |
| `MAX_BACKOFF_SECONDS` | `float = 30.0` |
| `RETRYABLE_EXCEPTIONS` | `tuple[type[Exception], ...] = (peewee.OperationalError, peewee.InterfaceError)` |
| `DATABASE_URL_ENV` | `str = "DATABASE_URL"` |
| `ALLOWED_URL_SCHEMES` | `tuple[str, ...] = ("postgres", "postgresql", "sqlite")` |
| `ERROR_LOG_PATH` | `Path = Path("error_log.txt")` |
| `EXTRAS_PRICES_PATH` | `Path` = `extras_prices.json` next to `db.py` |
| `TABLE_NAME` | `str = "listing"` |
| `FIXED_COLUMNS` | `tuple[str, ...]`: `id, ad_key, title, price_gbp, year, miles, fuel_type, detail_url, make, model, model_type, postcode, extras_total, extras_other, first_found, last_found, days_live` (in this order) |
| `FALLBACK_KEY_PREFIX` | `str = "nokey_"` |

Module-level objects, built at import:

- `database: peewee.DatabaseProxy`
- `EXTRAS_PRICES: dict[str, dict[str, int]]` and `EXTRA_COLUMNS: list[str]`, from `load_extras_prices()`
- `EXTRA_COLUMN_MAP: dict[str, str]`: canonical name to column name, in `EXTRA_COLUMNS` order
- `class BaseModel(peewee.Model)` with `Meta.database = database`
- `class UtcDateTimeField(peewee.DateTimeField)` (D6)
- `class ListingBase(BaseModel)`: the fixed fields from design section 4, using the D7 types. `price_gbp` is `IntegerField()`. `year` and `miles` are `IntegerField(null=True)`. `extras_total` and `days_live` are `IntegerField(default=0)`. `first_found` and `last_found` are `UtcDateTimeField()`.
- `Listing: type[ListingBase]`: the dynamic subclass with one `IntegerField(default=0)` per `EXTRA_COLUMN_MAP` value. Table name is `"listing"`.

Exceptions:

- `class ConfigError(RuntimeError)`: missing or invalid `DATABASE_URL`. The message contains the text `DATABASE_URL` and `.env.template` and never the value.
- `class SchemaError(RuntimeError)`: missing table or columns. The message contains `` `just db-init` `` and lists the missing column names, or says the table is missing.

Dataclasses and types:

- `@dataclass(frozen=True) class SaveResult`: `succeeded: bool`, `attempts: int`, `saved: int`, `skipped: int`
- `class ListingRow(TypedDict)` (the row dict shape, see 1.3)

Functions (Google-style docstrings required):

| Signature | Behaviour |
| --------- | --------- |
| `snake_case(name: str) -> str` | Design section 5 rule. All 5 sample rows in that table must match exactly. |
| `build_extra_column_map(names: Iterable[str]) -> dict[str, str]` | Order-preserving. Raises `ValueError` on collision (D9). |
| `load_extras_prices(path: Path = EXTRAS_PRICES_PATH) -> tuple[dict[str, dict[str, int]], list[str]]` | D10 |
| `get_database_url(env: Mapping[str, str] \| None = None) -> str` | Reads `env` (default `os.environ`). Strips whitespace. Validates the scheme (D5). Raises `ConfigError`. |
| `connect_from_env(env: Mapping[str, str] \| None = None) -> peewee.Database` | Calls `get_database_url`, then `playhouse.db_url.connect(url, autoconnect=False)`. Never logs the URL. |
| `bind_database(db: peewee.Database) -> None` | `database.initialize(db)` |
| `init_db(db: peewee.Database \| None = None) -> peewee.Database` | If `db` is None, uses `connect_from_env()`. Then binds, `connect(reuse_if_open=True)`, `check_schema()`, and returns the database. Issues no DDL. |
| `check_schema() -> None` | Read-only. Uses `database.table_exists(TABLE_NAME)` and `database.get_columns(TABLE_NAME)`. Raises `SchemaError` if the table or any model column is missing. Extra live columns (orphans) are allowed. |
| `close_db() -> None` | Closes the connection if the proxy is initialised and open. Safe to call twice. |
| `utc_now() -> datetime` | `datetime.now(UTC)` |
| `extract_ad_key(detail_url: str \| None) -> str \| None` | `urlsplit`. Takes the last non-empty path segment (the query and fragment are ignored). Returns it only if it fullmatches `\d{15}`. Otherwise returns `None`. |
| `fallback_ad_key(title: str \| None, year: int \| None, miles: int \| None, fuel_type: str \| None) -> str` | `"nokey_" + sha1("title\|year\|miles\|fuel_type").hexdigest()`. Each `None` becomes `""`. Uses the normalised values. Deterministic. |
| `parse_price_gbp(text: str \| None) -> int \| None` | Strips `£`, commas and spaces. `"£42,000"` gives `42000` and `"42000"` gives `42000`. Returns `None` if unparseable. |
| `parse_year(text: str \| None) -> int \| None` | First `\b(19\|20)\d{2}\b`. `"2021 (21 reg)"` gives `2021`. `"NA"` gives `None`. |
| `parse_miles(text: str \| None) -> int \| None` | Digits from `"12,345 miles"` give `12345`. `"NA"` or `None` give `None`. |
| `backoff_seconds(attempt: int) -> float` | `min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2 ** (attempt - 1))`, where `attempt` starts at 1 |
| `append_error_log(message: str, path: Path \| str = ERROR_LOG_PATH) -> None` | Appends one UTC-timestamped line. |
| `save_listings(rows: Sequence[ListingRow], *, clock: Callable[[], datetime] = utc_now, sleep: Callable[[float], None] = time.sleep, error_log_path: Path \| str = ERROR_LOG_PATH) -> SaveResult` | See 1.4 |

### 1.2 `db_init.py` (repo root, top-level module `db_init`)

- `DB_INIT_LOCK_KEY: int`: a fixed 64-bit key for `pg_advisory_xact_lock`.
- `@dataclass(frozen=True) class SyncReport`: `created_table: bool`, `added_columns: list[str]`, `orphan_columns: list[str]`.
- `sync_schema(db: peewee.Database) -> SyncReport`. Runs in a single `db.atomic()`. If the database is Postgres, the first statement is `SELECT pg_advisory_xact_lock(%s)`. This is skipped on SQLite. Then: create the table only if it is absent (D14). Diff `Listing` columns against `db.get_columns("listing")`. A missing fixed column raises `SchemaError` (D13). Each missing extra column gets an `ALTER TABLE ... ADD COLUMN ... INTEGER NOT NULL DEFAULT 0`, in `EXTRA_COLUMN_MAP` order (D15). Orphan columns are logged at WARNING and left alone. Nothing is dropped, renamed or retyped.
- `run(db: peewee.Database | None = None) -> int`. If `db` is None, uses `db.connect_from_env()`. Binds the proxy and connects. Calls `sync_schema`. Logs a summary (counts and column names, never the URL). Closes in `finally`. Returns `0` on success, including a no-op. Returns `1` on `ConfigError`, `SchemaError` or `peewee.DatabaseError`, after logging an actionable message. It does not raise.
- `main() -> None`: calls `load_dotenv()`, configures console logging, then `raise SystemExit(run())`. Runs under `if __name__ == "__main__":`.

### 1.3 Row dict shape: `db.ListingRow` (TypedDict, `total=True`)

| Key | Type | Source in `auto_trader.get_total_pages` |
| --- | ---- | -------------------------------------- |
| `title` | `str \| None` | `get_title()` |
| `price` | `str \| None` | `get_price()` (raw text: digits or `£42,000`) |
| `year` | `str \| None` | `get_other_car_data()`, may be `"NA"` |
| `miles` | `str \| None` | `get_other_car_data()`, may be `"NA"` |
| `fuel_type` | `str \| None` | `get_other_car_data()`; `"NA"` is stored as NULL |
| `detail_url` | `str \| None` | the cleaned title-anchor href |
| `make` | `str` | `make` (the make.json `uriValue`) |
| `model` | `str \| None` | `model`; `""` is stored as NULL |
| `model_type` | `str \| None` | `trim` (`aggregatedTrim`); `""` is stored as NULL |
| `postcode` | `str` | the config `PostalCode` |
| `extras` | `dict[str, int]` | `get_extras()` matched output: canonical name to GBP |
| `extras_other` | `str` | `get_extras()` unmatched string; `""` is stored as NULL |

### 1.4 `save_listings` semantics

1. Normalise each row. Rows with no title, or an unparseable price, are skipped with a WARNING and counted in `skipped`. Extras are mapped through `EXTRA_COLUMN_MAP`. An unknown canonical name is logged at WARNING and ignored. `extras_total` is the sum of the mapped extras. Every unmatched extra column is `0`.
2. `ad_key = extract_ad_key(detail_url)`. If that is `None`, use `fallback_ad_key(...)` and log a WARNING that contains `nokey_`.
3. Attempts run from `1` to `MAX_DB_ATTEMPTS`. Attempt 2 and later first call `database.close()` and `database.connect(reuse_if_open=True)`. Each attempt runs inside one `database.atomic()` and, for each row, uses `Listing.get_or_none(Listing.ad_key == key)`:
   - **Insert** sets `first_found = last_found = now` and `days_live = 0`.
   - **Update** sets every non-identity field from design section 6, `last_found = now`, and `days_live = (now - existing.first_found).days`. `first_found` is not touched.
   - `now = clock()` is read **once per attempt**.
4. On a `RETRYABLE_EXCEPTIONS` error: log at WARNING with the attempt number. If attempts remain, `sleep(backoff_seconds(attempt))`. The sleep sequence is `1.0, 2.0, 4.0`.
5. On success after one or more retries: log at INFO with the literal text `retry_count=<n>`, where `n = attempts - 1`. Return `SaveResult(True, attempts, saved, skipped)`.
6. When attempts are exhausted: call `logger.exception` at ERROR, run `append_error_log(...)` (the exception type and message, never the URL), and return `SaveResult(False, MAX_DB_ATTEMPTS, 0, skipped)`. **Do not raise.**
7. Non-retryable exceptions propagate. The page-level handler in `auto_trader` catches them.
8. An empty `rows` sequence returns `SaveResult(True, 0, 0, 0)` without touching the database.

### 1.5 `auto_trader.py` changes (contract-relevant)

- Removed: `import pandas`, `get_file_name()`, `save_data()`, and the `output_file` parameter.
- `@dataclass(frozen=True) class SearchConfig`: `postcode: str`, `page_number: int`, `year_from: str`, `year_to: str`, `radius: str`.
- `read_config(filename: str) -> list[SearchConfig]` uses stdlib `csv.DictReader`.
- `get_config(filename: str) -> None` iterates `read_config()` and `get_make_data()` and calls `get_total_pages(...)`. At the end it logs `"Scrape complete. %d listing(s) in database"` using `db.Listing.select().count()`.
- `get_total_pages(postcode, make, model, trim, fuel, year_from, year_to, radius, price_to, page_start=1) -> None` builds `ListingRow`s and calls `db.save_listings(rows)` once per page.
- `_configure_logging() -> None` (D11). `_load_extras_prices()` delegates to `db.load_extras_prices()` (D10).
- `main()` runs in this order: `_configure_logging()`, `load_dotenv()`, `db.init_db()`, `_load_extras_prices()`, create Firefox, `try: setup_cookies(); get_config(...)`, and in `finally` it calls `driver.quit()`. In an outer `finally` it calls `db.close_db()`.
- There are no routes and no HTTP payloads in this phase.

### 1.6 Test seams and fixtures (`tests/conftest.py`)

- `InstrumentedSqliteDatabase(peewee.SqliteDatabase)`:
  - `executed: list[str]` records every SQL string passed to `execute_sql`.
  - `fail_on(pattern: str, times: int, skip: int = 0, exc: type[Exception] = peewee.OperationalError) -> None` arms a fault. After skipping the first `skip` matching statements, the next `times` statements that match the regex `pattern` raise `exc`. It does not match `BEGIN`, `COMMIT`, `ROLLBACK` or `SAVEPOINT`.
  - `clear_faults() -> None`.
  - Faults are raised before delegating to `super().execute_sql`.
- Fixture `test_db(tmp_path)` returns `InstrumentedSqliteDatabase(tmp_path / "test.db")`. On teardown it calls `db.close_db()`.
- Fixture `schema_db(test_db)` asserts `db_init.run(test_db) == 0`, then calls `db.init_db(test_db)` and returns `test_db`. The schema is built by the same code `just db-init` runs.
- Fixture `make_row` is a factory that returns a valid `ListingRow` (`detail_url="https://www.autotrader.co.uk/car-details/202609206214030"`, `price="42000"`, and so on), with `**overrides`.
- Fixture `sleeps` returns a list and a recorder callable. Pass the callable as `sleep=`.
- Clock: tests pass `clock=lambda: T0`, then `clock=lambda: T1`, with fixed timezone-aware UTC datetimes.
- Autouse fixture: `monkeypatch.delenv("DATABASE_URL", raising=False)`, so no test ever sees a real URL.
- The error log is passed as `error_log_path=tmp_path / "error_log.txt"`.
- Pytest config (`pyproject.toml`): `[tool.pytest.ini_options] testpaths = ["tests"]` and `pythonpath = ["."]`.

### 1.7 Tests per acceptance scenario

| # | Scenario | Test file and function | Key assertions |
| - | -------- | ---------------------- | -------------- |
| 1 | Insert on first sight | `tests/test_upsert.py::test_insert_on_first_sight` | 1 row. `first_found == last_found == T0`. `days_live == 0`. `price_gbp == 42000`. Extras columns are correct and `extras_total` is their sum. |
| 2 | Re-sight preserves `first_found` | `tests/test_upsert.py::test_resight_updates_price_and_preserves_first_found` | Save at `T0`, then at `T1 = T0 + 3 days 5 hours` with a new price. Still 1 row. Price updated. `first_found == T0`. `last_found == T1`. `days_live == 3`. |
| 3 | Retry then succeed | `tests/test_retry.py::test_retry_then_succeed` | `fail_on(r"^\s*(SELECT\|INSERT\|UPDATE)", times=MAX_DB_ATTEMPTS - 1)`. `result.succeeded`. `result.attempts == MAX_DB_ATTEMPTS`. The row is persisted. Sleeps are `[1.0, 2.0, 4.0]`. `"retry_count=3"` appears in caplog. |
| 4 | Retry exhausted, run continues | `tests/test_retry.py::test_retry_exhausted_logs_error_and_continues` | Always fails. No exception raised. `succeeded is False`. An ERROR record exists. The error log file has a line. Sleeps are `[1.0, 2.0, 4.0]`. After `clear_faults()`, a second `save_listings` call succeeds, which proves the run continues on a usable connection. |
| 5 | Missing config fails fast | `tests/test_config.py::test_missing_database_url_fails_fast` (parametrised: unset, `""`, `"   "`); `::test_invalid_database_url_does_not_leak_value` (uses a `mysql://u:SENTINEL-s3cret@h/d` URL; asserts the sentinel is absent from the exception text and from caplog); `::test_main_fails_before_launching_browser` (monkeypatches `auto_trader.load_dotenv` to a no-op and `auto_trader.webdriver.Firefox` to fail the test if called; `auto_trader.main()` raises `ConfigError`) | The message contains `DATABASE_URL` and `.env.template`. |
| 6 | No spreadsheet produced | `tests/test_no_spreadsheet.py::test_import_does_not_load_pandas_or_openpyxl` (a subprocess `sys.executable -c "import auto_trader, db, db_init, sys; ..."` with cwd at the repo root; asserts neither module is in `sys.modules`); `::test_get_config_run_writes_db_not_xlsx` (`monkeypatch.chdir(tmp_path)`; write a config CSV; monkeypatch `get_make_data` and `get_total_pages` so the fake calls `db.save_listings([make_row()])`; run `get_config`; assert no `*.xlsx` in `tmp_path`, 1 row in the database, and `auto_trader` has no `save_data` or `get_file_name` attribute) | |
| 7 | `ad_key` fallback | `tests/test_upsert.py::test_ad_key_fallback_when_url_has_no_id` (parametrised: `None`, a non-numeric path, a 14-digit id) | The stored `ad_key` starts with `nokey_` and equals `fallback_ad_key(...)`. A WARNING record mentions `nokey_`. Saving the same row twice still leaves 1 row. |
| 8 | `db-init` creates the schema | `tests/test_db_init.py::test_db_init_creates_full_schema` | On an empty database, `run(...) == 0`. Live columns equal `FIXED_COLUMNS` plus every `EXTRA_COLUMN_MAP` value. `report.created_table`. |
| 9 | `db-init` is idempotent | `tests/test_db_init.py::test_db_init_is_idempotent` | Fill rows, then snapshot `SELECT * ORDER BY id` and the column list. Run again: returns 0, `added_columns == []`, no `CREATE`, `ALTER` or `DROP` in `executed` after the snapshot, and rows and columns are identical. |
| 10 | Adds only missing extras | `tests/test_db_init.py::test_db_init_adds_only_missing_extra_column` | After schema and rows, `ALTER TABLE listing DROP COLUMN <one extra>` (SQLite 3.35+). Run: that column is added and existing rows hold 0. Other columns and data are unchanged. `added_columns == [col]`. A second run gives `added_columns == []`. |
| 11 | Atomic and recoverable | `tests/test_db_init.py::test_db_init_rolls_back_on_failure_after_first_alter` | Drop two extra columns. `fail_on(r"^\s*ALTER TABLE", times=1, skip=1)`. `run(...) != 0`, and the column set equals the pre-run set (both still missing). `clear_faults()`, then `run(...) == 0` with both present. |
| 12 | Scraper never runs DDL | `tests/test_check_schema.py::test_init_db_fails_when_table_missing` and `::test_init_db_fails_when_extra_column_missing` | `db.init_db(test_db)` raises `SchemaError` containing `just db-init`. `executed` has no `CREATE`, `ALTER` or `DROP`. The schema is unchanged. |

Supporting tests (not scenarios, still required):

- `tests/test_helpers.py`:
  - `test_snake_case_samples`: all 5 rows from design section 5.
  - `test_extra_column_map_covers_all_extras`: the map length equals `len(EXTRA_COLUMNS)`, which is 53 today but must not be hardcoded. Assert it against the JSON.
  - `test_build_extra_column_map_rejects_collision`
  - `test_extract_ad_key` (parametrised, including a query string and a trailing slash)
  - `test_parse_price_year_miles` (parametrised)
  - `test_backoff_seconds`: 1, 2, 4, 8, 16, then capped at 30.
- `tests/test_db_init.py::test_db_init_warns_on_orphan_column_and_keeps_it`
- `tests/test_db_init.py::test_db_init_skips_advisory_lock_on_sqlite`: no `pg_advisory` in `executed`.

---

## 2. Ordered steps

Legend: **[P]** means the step can run in parallel with the other [P] steps in the same group.

### Group A: verify and set up (sequential)

**A1. Verify the live APIs with Context7.** This is required by design section 2 and open question 2. Use `resolve-library-id` and `query-docs` for `peewee` and `supabase`. Confirm:

- `playhouse.db_url.connect`: support for the `postgres`, `postgresql` and `sqlite` schemes, passing `autoconnect=False`, and whether URL query parameters such as `sslmode=require` reach psycopg2.
- `DatabaseProxy` forwarding of `connect`, `close`, `is_closed`, `atomic`, `table_exists`, `get_columns`, `execute_sql` and `create_tables`.
- The `get_columns()` return shape (`ColumnMetadata.name`).
- That DDL inside `atomic()` is rolled back on SQLite under Peewee's autocommit or isolation handling.
- The Supabase transaction-pooler URI shape (host, port `6543`, user `postgres.<ref>`) and psycopg2 prepared-statement behaviour on that pooler.
- That `python-dotenv` `load_dotenv(override=False)` keeps an existing empty `DATABASE_URL`.

Record any deviation from this plan as a note at the top of this file before coding. Do not paste credentials anywhere.

**A2. `pyproject.toml`.** Add `peewee`, `psycopg2-binary` and `python-dotenv` to `[project].dependencies`. Remove `openpyxl` and `pandas`. Add `pytest` to `[dependency-groups].dev`. Add `[tool.pytest.ini_options]` with `testpaths = ["tests"]` and `pythonpath = ["."]`. Run `uv lock` and `just sync`. (This is the only command step in the plan. It is the implementer's job, not the planner's.)

### Group B: tests first (test-writer, against section 1; red until Group C lands)

**B1.** `tests/__init__.py` (empty) and `tests/conftest.py` (section 1.6).

**B2 [P].** `tests/test_helpers.py`, `tests/test_upsert.py`, `tests/test_retry.py`.

**B3 [P].** `tests/test_config.py`, `tests/test_no_spreadsheet.py`.

**B4 [P].** `tests/test_db_init.py`, `tests/test_check_schema.py`.

### Group C: implementation

**C1. `db.py`.** This depends on A2. Build it in this order: constants and exceptions; `snake_case`, `build_extra_column_map`, `load_extras_prices`; the proxy, `BaseModel`, `UtcDateTimeField`, `ListingBase`, and dynamic `Listing`; the config functions (`get_database_url`, `connect_from_env`, `bind_database`, `init_db`, `check_schema`, `close_db`); the parse and key helpers; `append_error_log`; `save_listings` with the retry loop. Access dynamic fields through `getattr(Listing, col)`, or field kwargs in `create`/`update`. Use targeted `# type: ignore[unresolved-attribute]` only where `ty` still complains. No blanket disables.

**C2. `db_init.py`.** This depends on C1. Implement section 1.2. Use `isinstance(database.obj, peewee.PostgresqlDatabase)` (or the A1-verified equivalent) to gate the advisory lock.

**C3. `auto_trader.py`.** This depends on C1 and can run in parallel with C2. Implement section 1.5:

- Delete pandas, `get_file_name` and `save_data`. Drop the now-unused `datetime` import.
- Switch config reading to `csv.DictReader`.
- Build `ListingRow`s in Pass 1 and Pass 2. Remove the Extras_Total calculation from Pass 2; `save_listings` computes it.
- Make the page-level `except` use `db.append_error_log`.
- Reorder `main()` per D4 and D11.
- Keep the `make.json` `[::-1]` order, the 101-page cap, the two-pass flow and the cookie setup unchanged.

**C4.** Run `just fix`, `just check` and `just test` until green. Every scenario test in section 1.7 must pass. Fix the code, not the tests, unless a test contradicts this contract.

### Group D: config, tooling and docs (all [P]; can start after A2, finalise after C4)

**D1 [P]. `Justfile`.**

- Add a `[group('db')] db-init` recipe that runs `uv run python db_init.py`, with the design's comment and a "requires DATABASE_URL (see .env.template)" prerequisite comment.
- Add a `[group('quality')] test` recipe that runs `uv run pytest -q`.
- Change `ci: sync check pysentry` to `ci: sync check test pysentry`. `build: ci semgrep` then runs tests automatically. Tests need no `DATABASE_URL`, so CI stays hermetic.
- `clean`: drop `autotrader_info_*.xlsx`. Keep `error_log.txt` and `scraper.log`.
- Run `just --fmt --check`.

**D2 [P]. `.env.template`.**

- One variable, `DATABASE_URL`, with a comment block: required; the Supabase **transaction-mode pooler** URI; the shape `postgresql://postgres.<project-ref>:<password>@<pooler-host>.pooler.supabase.com:6543/postgres?sslmode=require` (adjusted to what A1 confirms); `sslmode=require` is mandatory; `sqlite:///local.db` is accepted for local testing only; never commit real values; `cp .env.template .env`.
- Use only placeholders, so the betterleaks pre-commit hook does not fire.

**D3 [P]. `.gitignore`.** Add `*.db` and `*.sqlite3` (D19). Leave the `*.xlsx` ignore in place for historical files.

**D4 [P]. `TECH_DEBT.md` (new).** Add the five entries from the design's TECH_DEBT list, each with a short rating (low, medium or high). Also add:

- D6, the custom `UtcDateTimeField` that stores ISO strings.
- D13, fixed-column drift is not auto-migrated.
- No Postgres integration test in CI (tests are SQLite only); Supabase is checked through the human-run `verification.md` steps.
- The `sqlite:///` scheme is accepted for local use (D5).
- The single connection per run, with no in-process pool.

**D5 [P]. AgentSoul**, edited directly in `/Users/bennewton/Ai/AgentSoul/AgentSoul/`, never committed from this repo.

- Create `Peewee_ORM_Choice.md` with the frontmatter `type: decision`, `tags` (python, orm, database, peewee, serverless) and `status: active`, and the title `# Peewee ORM Choice`. Content per the design, plus the reusable patterns proven here:
  - a `DatabaseProxy` for test injection
  - a file-backed SQLite under tmp_path for retry tests
  - an additive-only `db_init` inside one transaction, with a Postgres advisory xact lock
  - dynamic columns via `type()`
  - Include `## Related` (`[[Approved_Dependencies]]`, `[[Environment_Contract]]`, `[[env_file_usage]]`, `[[Secrets_Management]]`, `[[logging_conventions]]`) and `## Update trigger`.
- `AgentSoul_Index.md`: add the page. Use a new `## Data & persistence` section or the `Testing & quality` neighbour; prefer the new section.
- `Approved_Dependencies.md`: add `peewee` alphabetically between `pandas` and `pillow`. Do **not** add `psycopg2-binary`.

**D6 [P]. `README.md`.**

- Replace the xlsx output description and the "Output Spreadsheet Columns" section with the `listing` table (snake_case columns, `ad_key` upsert identity, `first_found`/`last_found`/`days_live`).
- Add a "Database setup" section: `cp .env.template .env`, set `DATABASE_URL`, `just db-init`.
- Update the command table with `test`, `db-init` and `clean`.
- Update the project structure with `db.py`, `db_init.py`, `tests/`, `.env.template` and `TECH_DEBT.md`.
- Drop the dedup/NaN feature bullet.
- State that extras number 53 (Porsche Taycan, 2026) and that columns are derived from `extras_prices.json`.

**D7 [P]. `.github/copilot-instructions.md` (AGENTS.md).** If `AGENTS.md` is a symlink, one edit covers both. If it is a separate file, edit both identically.

- Data flow: `main()` runs `init_db`; there is no `get_file_name` or `save_data`; `save_listings`.
- Remove `openpyxl` and `selenium`/`openpyxl` from the requirements note. Add peewee, psycopg2-binary and python-dotenv.
- Commands table: add `just test` and `just db-init`.
- "Output" and "Data Layout": the single `listing` table and snake_case columns.
- The `just clean` description.
- Line length is 150.
- The dedup convention is now the `ad_key` upsert.
- Key Files: `db.py`, `db_init.py`, `.env.template`.

**D8. Design frontmatter.** Add `uat: guide` to `0002-Database-and-ORM.md` (D18). Do not change the design's behaviour.

### Group E: UAT guide (uat-author; after C4, can run in parallel with D)

**E1. `docs/specs/0002-Database-and-ORM/verification.md`.** Use fenced `bash` blocks with trailing newlines, and an "Expected results" table per section.

- **Part 1, local and runnable by the agent (no Supabase).**
  1. `just sync && just check && just test`: all green.
  2. `grep -nE "pandas|openpyxl|xlsx" auto_trader.py pyproject.toml`: no matches.
  3. `DATABASE_URL= uv run python db_init.py; echo "exit=$?"`: non-zero, and the message names `DATABASE_URL` and `.env.template`.
  4. `DATABASE_URL='mysql://u:SENTINEL@h/d' uv run python db_init.py 2>&1 | grep -c SENTINEL`: prints `0`.
  5. `rm -f verify.db && DATABASE_URL=sqlite:///verify.db uv run python db_init.py`: exit 0, and the log says the table was created.
  6. Run it again: exit 0 with "no changes".
  7. `sqlite3 verify.db "PRAGMA table_info(listing);" | wc -l`: equals 17 plus the extras count. Show how the count is derived with `uv run python -c "import db; print(len(db.FIXED_COLUMNS) + len(db.EXTRA_COLUMN_MAP))"`.
  8. `sqlite3 verify.db "ALTER TABLE listing DROP COLUMN ambient_lighting;"`, then re-run `db_init`: it reports `added ambient_lighting`.
  9. Clean up with `rm -f verify.db`.
- **Part 2, local scraper against SQLite. HUMAN-RUN** (needs Firefox and geckodriver and makes live AutoTrader requests).
  1. `DATABASE_URL=sqlite:///verify.db uv run python db_init.py`
  2. `DATABASE_URL=sqlite:///verify.db just run`. Let one page complete, or let the single-make config run to the end.
  3. `ls *.xlsx`: none.
  4. `sqlite3 verify.db "SELECT ad_key, price_gbp, first_found, last_found, days_live FROM listing LIMIT 5;"`
  5. Re-run the scrape, then confirm the row count is unchanged for re-seen adverts and `last_found` has advanced.
- **Part 3, real Supabase. HUMAN-RUN ONLY** (needs real credentials, which agents must not read).
  1. `cp .env.template .env`, then fill in the pooler URL.
  2. `just db-init` twice; the second run is a no-op.
  3. In the Supabase dashboard SQL editor for project `AutoTrader_Scraper`, check the columns: `select column_name, data_type from information_schema.columns where table_name='listing' order by ordinal_position;`. `first_found` and `last_found` should be `timestamp with time zone`, and the extras `integer`.
  4. `just run`. Afterwards check `select count(*), count(distinct ad_key) from listing;` (the two counts should be equal).
  5. Second run: `first_found` is unchanged and `last_found` is bumped.
  6. Confirm no `.xlsx` exists and `scraper.log` contains no `postgresql://`: `grep -c "postgresql://" scraper.log` gives `0`.
- State clearly that this guide is a sanity layer, and the proof is the section 1.7 tests.

**E2.** The verifier runs `validate-verification` on Part 1 verbatim. Parts 2 and 3 are flagged as human-run and are not run by agents.

### Group F: validate and record state (sequential, last)

**F1.** Run `just build` (which is `ci` plus semgrep, and `ci` now includes `test`) and `just --fmt --check`. Everything must be green.

**F2.** Human: complete `verification.md` Parts 2 and 3. That means `just db-init` twice against Supabase, then one real scrape with upserts confirmed and no `.xlsx` produced.

**F3. `STATE.md`.** Only after C4, F1 and E2 are green, and the human has confirmed F2:

- Set 0002 to `done`, with a one-line summary: Peewee and Supabase `listing` table, upsert on `ad_key`, `just db-init`, `just test` in CI. Link to `TECH_DEBT.md`.
- If F2 is still pending, set it to `implemented — awaiting Supabase UAT` instead of `done`.
- Do not mark 0003.

---

## 3. Dependency graph (summary)

```
A1 -> A2 -> B1 -> (B2 | B3 | B4)
             A2 -> C1 -> (C2 | C3) -> C4
             A2 -> (D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8)   [finalise D after C4]
                        C4 -> E1 -> E2
             C4 + D* + E2 -> F1 -> F2 (human) -> F3
```

## 4. Out of scope (unchanged from the design)

- Vercel and the scheduled run.
- Migrating historical xlsx data.
- RLS and auth.
- Destructive migrations and `ON CONFLICT`.
- Moving logging to stdout JSON.
- Running `db-init` in CI or deploy.
