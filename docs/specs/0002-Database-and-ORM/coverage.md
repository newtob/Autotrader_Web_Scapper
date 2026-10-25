# Coverage 0002: Database and ORM

Acceptance test plan for [`0002-Database-and-ORM.md`](0002-Database-and-ORM.md) (design) against
the test contract in [`plan.md`](plan.md) section 1. "Verified" means the named test passes in
`just test` (run by `just ci`). Nothing here is a manual step; the human-run Supabase and Firefox
checks live in `verification.md` and are a sanity layer only.

**Determinism rules (all rows):** no network, no Firefox, no Supabase. The database is
`InstrumentedSqliteDatabase(tmp_path / "test.db")` (D2). `DATABASE_URL` is deleted by the autouse
fixture. Time comes from `clock=lambda: T0`/`T1` (fixed tz-aware UTC). Backoff goes through the
`sleeps` recorder. `error_log_path=tmp_path / "error_log.txt"`, or `monkeypatch.chdir(tmp_path)`
where the production default path is exercised. `load_dotenv` is monkeypatched to a no-op wherever
`main()` runs.

**Rungs:** `unit` = pure function. `component` = `db`/`db_init` against SQLite file.
`seam` = `auto_trader` code calling the real `db.save_listings` into SQLite, browser faked.
`process` = subprocess import. `static` = file or signature inspection.

**Tier:** `core` rows prove a design scenario or the scraper wiring; each names the production line
whose removal must turn it red (the spec-auditor's negative control). `edge` rows pin awkward but
plausible inputs.

| ID | Tier | Scen. | Test (`file::function`) | Rung | Seam / fakes | Key assertions (incl. awkward shapes) | Negative control: removing this must turn it red |
| -- | ---- | ----- | ----------------------- | ---- | ------------ | ------------------------------------- | ------------------------------------------------ |
| **`tests/test_helpers.py`** | | | | | | | |
| H1 | edge | - | `test_snake_case_samples` | unit | none | All 5 design section 5 samples exact; `4+1 ...` gives `extra_4_1_...`; runs of punctuation collapse to one `_`. | Digit-prefix `extra_` branch in `snake_case`. |
| H2 | edge | - | `test_extra_column_map_covers_all_extras` | unit | real `extras_prices.json` | `len == len(EXTRA_COLUMNS)` where the expected count is computed by the test from the JSON union (never `53`); order equals `EXTRA_COLUMNS`; values unique, match `^[a-z_][a-z0-9_]*$`, disjoint from `FIXED_COLUMNS`. | Map built from `EXTRA_COLUMNS`. |
| H3 | edge | - | `test_build_extra_column_map_rejects_collision` | unit | none | `["Heated Seats", "heated-seats"]` raises `ValueError`; `["Title"]` (collides with fixed `title`) raises `ValueError` (D9). | Collision check in `build_extra_column_map`. |
| H4 | edge | - | `test_extract_ad_key` (param) | unit | none | Gives the id for: absolute URL; relative `/car-details/<id>`; `?advertising-location=at_cars&sort=relevance`; trailing `/`; `#gallery`. Gives `None` for: `None`, `""`, 14 digits, 16 digits, `/car-details/<id>/photos` (last segment rule). | `fullmatch(r"\d{15}")`. |
| H5 | edge | - | `test_parse_price_year_miles` (param) | unit | none | Price: `"£42,000"`, `"42000"`, `" £ 42,000 "` give `42000`; `"POA"`, `""`, `None` give `None`. Year: `"2021 (21 reg)"` gives `2021`; `"NA"`, `"21 reg"` give `None`. Miles: `"12,345 miles"` gives `12345`; `"1 mile"` gives `1`; `"NA"`, `None` give `None`. | n/a |
| H6 | edge | - | `test_backoff_seconds` | unit | none | Attempts 1-7 give `1, 2, 4, 8, 16, 30, 30`. | `min(MAX_BACKOFF_SECONDS, ...)` cap. |
| H7 | edge | - | `test_fallback_ad_key_is_deterministic` | unit | none | Same inputs give the same key; `None` and `""` are equal; changing `miles` changes the key; format `nokey_` + 40 hex. | n/a |
| H8 | edge | - | `test_load_extras_prices_missing_file` | unit | `tmp_path` | A missing path gives `({}, [])` plus a WARNING record (D10). | n/a |
| **`tests/test_upsert.py`** | | | | | | | |
| U1 | core | 1 | `test_insert_on_first_sight` | component | `schema_db`, `make_row`, `clock=T0` | 1 row; `first_found == last_found == T0` (tz-aware UTC); `days_live == 0`; `price_gbp == 42000`; one extra set to its price, other extras `0`; `extras_total` equals their sum; `model=""` and `extras_other=""` stored as NULL; `SaveResult(True, 1, 1, 0)`. | Insert branch `first_found = last_found = now, days_live = 0` in `save_listings`; `extras_total` sum. |
| U2 | core | 2 | `test_resight_updates_price_and_preserves_first_found` | component | `schema_db`, `clock=T0` then `T1 = T0 + 3d5h` | Still 1 row; new price; `first_found == T0`; `last_found == T1`; `days_live == 3` (not 4: floor of whole days); changed extras and `postcode` are updated. | Update branch leaving out `first_found`; `days_live = (now - existing.first_found).days`. |
| U3 | core | 7 | `test_ad_key_fallback_when_url_has_no_id` (param: `None`, `/car-details/not-an-id`, 14-digit id, `/<id>/photos`) | component | `schema_db` | Stored `ad_key` starts with `nokey_` and equals `fallback_ad_key(title, 2021, miles, fuel)` on the normalised values; a WARNING record contains `nokey_`; saving the same row again leaves 1 row. | The `extract_ad_key(...) is None -> fallback_ad_key(...)` branch and its WARNING. |
| U4 | edge | - | `test_rows_without_title_or_unparseable_price_are_skipped` | component | `schema_db` | Batch `[ok, title=None, price="POA"]` gives `saved == 1`, `skipped == 2`, and 2 WARNINGs. | Skip branch in row normalisation. |
| U5 | edge | - | `test_unknown_extra_name_is_ignored_with_warning` | component | `schema_db` | `extras={"Not A Real Extra": 999, <EXTRA_COLUMNS[0]>: 100}` gives `extras_total == 100`; WARNING names the unknown key. | n/a |
| U6 | edge | - | `test_timestamps_round_trip_as_utc` | component | `clock` returns a `+01:00` datetime | Read back equals the same instant; `utcoffset() == 0` (D6 `UtcDateTimeField`). | `python_value` UTC conversion. |
| U7 | edge | - | `test_mixed_batch_new_and_existing` | component | `clock=T0`, `T1` | At `T1`, `[A (existing), B (new)]` gives A `first_found == T0`, B `first_found == T1`; count 2. | n/a |
| U8 | edge | - | `test_empty_rows_is_noop` | component | `test_db.executed` | `SaveResult(True, 0, 0, 0)`; `executed` unchanged. | n/a |
| U9 | edge | - | `test_na_values_stored_as_null` | component | `schema_db` | `year="NA"`, `miles="NA"`, `fuel_type="NA"` give NULLs; the row is still stored. | n/a |
| **`tests/test_retry.py`** | | | | | | | |
| R1 | core | 3 | `test_retry_then_succeed` (param `exc`: `OperationalError`, `InterfaceError`) | component | `fail_on(r"^\s*(SELECT\|INSERT\|UPDATE)", times=MAX_DB_ATTEMPTS-1, exc=exc)`, `sleeps`, `clock`; spy on `test_db.close` | `succeeded`; `attempts == MAX_DB_ATTEMPTS`; row persisted once; `sleeps == [1.0, 2.0, 4.0]`; `close` called `MAX_DB_ATTEMPTS-1` times (reconnect); an INFO record contains `retry_count=3`. | `except RETRYABLE_EXCEPTIONS` branch; `database.close()` and `connect(reuse_if_open=True)` on attempt 2 and later; `sleep(backoff_seconds(attempt))`. |
| R2 | core | 4 | `test_retry_exhausted_logs_error_and_continues` | component | always fail, `sleeps`, `error_log_path` | No raise; `SaveResult(False, MAX_DB_ATTEMPTS, 0, 0)`; an ERROR record with `exc_info`; error log has 1 line containing `OperationalError`; `sleeps == [1.0, 2.0, 4.0]` (no sleep after the last attempt); after `clear_faults()` a second call succeeds. | `return SaveResult(False, ...)` instead of raising; the `append_error_log(...)` call. |
| R3 | edge | - | `test_exhausted_mid_batch_leaves_no_partial_rows` | component | 2-row batch, `fail_on(r"^\s*INSERT", times=MAX_DB_ATTEMPTS, skip=1)` | 0 rows in the table; `saved == 0` (whole batch in one `atomic()`, D16). | `database.atomic()` around the attempt. |
| R4 | edge | - | `test_non_retryable_error_propagates` | component | `fail_on(INSERT, times=1, exc=IntegrityError)` | Raises `IntegrityError`; `sleeps == []`. | n/a |
| R5 | edge | - | `test_failed_reconnect_counts_as_attempt` | component | `test_db.connect` raises `OperationalError` once on attempt 2 | `succeeded`; `attempts == 3`; `sleeps == [1.0, 2.0]` (D16: reconnect is inside the guarded block). | n/a |
| R6 | edge | - | `test_first_attempt_success_has_no_retry_log` | component | `sleeps` | `attempts == 1`; `sleeps == []`; no `retry_count=` in caplog. | n/a |
| **`tests/test_config.py`** | | | | | | | |
| C1 | core | 5 | `test_missing_database_url_fails_fast` (param: unset, `""`, `"   "`) | unit | env mapping and `os.environ` via monkeypatch | Both `db.get_database_url()` and `db.init_db()` raise `ConfigError`; the message contains `DATABASE_URL` and `.env.template`. | Remove both the empty-value check and the scheme allowlist in `get_database_url` (each alone leaves another path to `ConfigError`). |
| C2 | core | 5 | `test_invalid_database_url_does_not_leak_value` (param: `mysql://u:SENTINEL-s3cret@h/d`, bare `SENTINEL-s3cret`) | unit | caplog at DEBUG | `ConfigError`; sentinel absent from `str(exc)`, `repr(exc)`, the full `traceback.format_exception(exc)` (no chained cause carrying the value), and every caplog message. | Scheme allowlist check; `raise ... from None` (or no chaining). |
| C3 | core | 5, 12 | `test_main_fails_before_launching_browser` (param: URL unset gives `ConfigError`; `sqlite:///<tmp>/empty.db` gives `SchemaError` containing `just db-init`) | seam | `auto_trader.load_dotenv` no-op; `auto_trader.webdriver.Firefox` fails the test if called; `chdir(tmp_path)` | `auto_trader.main()` raises the expected error; Firefox never constructed; the empty db has no `listing` table afterwards. | `db.init_db()` placed before `webdriver.Firefox(...)` in `main()` (D4). |
| C4 | edge | - | `test_accepted_url_schemes` | unit | env mapping | `postgres://`, `postgresql://`, `sqlite:///x.db`, each with surrounding whitespace, return the stripped value. | n/a |
| C5 | edge | - | `test_connect_from_env_postgres_is_lazy_and_never_logs_url` | unit | env `postgresql://postgres.ref:SENTINEL@h.pooler.supabase.com:6543/postgres?sslmode=require` | Returns a `PostgresqlDatabase`; `is_closed()` (no network); sentinel absent from caplog at DEBUG. | n/a |
| C9 | edge | 5 | `test_postgres_url_defaults_sslmode_to_require`, `test_explicit_sslmode_and_sqlite_are_left_alone`, `test_connect_from_env_passes_sslmode_require_to_postgres` | unit | env mapping; `connect_params` inspected, no connection | Postgres URLs get `sslmode=require` unless already set; sqlite untouched. | Remove `with_default_sslmode` from `connect_from_env`. |
| C6 | edge | - | `test_db_module_does_not_load_dotenv` | unit | `chdir(tmp_path)` with a `.env` containing `DATABASE_URL=sqlite:///x.db` | `db.connect_from_env()` still raises `ConfigError` (D3). | n/a |
| C7 | edge | - | `test_db_init_run_returns_1_on_config_error` | component | no env | `db_init.run()` returns `1`, does not raise; caplog names `DATABASE_URL`. | n/a |
| C8 | edge | - | `test_main_order_and_cleanup_on_error` | seam | schema'd sqlite URL; recording `FakeFirefox`; `setup_cookies` and `get_config` recorders; `get_config` raises `RuntimeError` | Event order `init_db, Firefox, setup_cookies, get_config, quit`; `RuntimeError` propagates; `db.database.is_closed()` afterwards (`close_db` in outer `finally`). | n/a |
| **`tests/test_no_spreadsheet.py`** | | | | | | | |
| N1 | core | 6 | `test_import_does_not_load_pandas_or_openpyxl` | process | `subprocess.run([sys.executable, "-c", ...])`, `cwd=tmp_path`, `PYTHONPATH=<repo>`, env without `DATABASE_URL` | Exit 0 (import needs no URL, D1); `pandas` and `openpyxl` not in `sys.modules` after importing `auto_trader, db, db_init`; no `scraper.log` created in `tmp_path` (D11). | `import pandas as pd` in `auto_trader.py`; module-level `FileHandler`. |
| N2 | core | 6 | `test_get_config_run_writes_db_not_xlsx` | seam | `chdir(tmp_path)`; real config CSV header; fake `get_make_data`; fake `get_total_pages` calls `db.save_listings([make_row()])` | No `*.xlsx` in `tmp_path`; 1 row in the db; fake received `page_start == 1` (int) and no `output_file`; INFO `Scrape complete. 1 listing(s) in database`; `auto_trader` has no `save_data` or `get_file_name`. | `get_file_name()` and `save_data()` deletion; `Listing.select().count()` summary. |
| N5 | edge | 6 | `test_summary_count_failure_logs_warning_and_finishes`, `test_summary_count_reconnects_after_dropped_connection` | seam | `get_config` with injected `SELECT COUNT` faults | Count failure after reconnect logs a WARNING and returns; a single drop reconnects and logs the count. | Remove the reconnect/try in `_log_scrape_summary`. |
| N3 | edge | - | `test_dependencies_drop_pandas_and_openpyxl` | static | `tomllib` on `pyproject.toml` | No `pandas` or `openpyxl`; has `peewee`, `psycopg2-binary`, `python-dotenv`; `pytest` in dev. | n/a |
| N4 | edge | - | `test_read_config_with_stdlib_csv` (param: LF; CRLF with UTF-8 BOM, as Excel saves it) | unit | `tmp_path` CSV | 2 rows give `SearchConfig` with `page_number` as `int` and the other fields as `str`; the BOM variant parses identically (parity with the `pd.read_csv` it replaces; needs `encoding="utf-8-sig"`). | n/a |
| **`tests/test_scraper_wiring.py`** (+ helper `tests/scraper_fakes.py`) | | | | | | | |
| W1 | core | wiring | `test_get_total_pages_saves_page_rows_via_save_listings` | seam | `FakeDriver` set on `auto_trader.driver` (`raising=False`), serving HTML with `pagination-show` `Page 1 of 1` and 3 cards; `auto_trader.WebDriverWait` stub; `time.sleep` no-op; `scroll_to_load_all` returns 0; `get_extras` returns `({EXTRA_COLUMNS[0]: 100}, "Heated Steering")`; `db.save_listings` wrapped (partial with `clock`, `sleeps`, `error_log_path`, plus a call counter) then delegates to the real function | Real `get_title`, `get_price`, `get_other_car_data`, `_title_matches`. Cards: Taycan with href `/car-details/202609206214030?advertising-location=at_cars&sort=relevance`, `£42,000`, `2021 (21 reg)`, `12,345 miles`, `Electric`; a second Taycan; a `BMW i4` card that is filtered out. Result: 2 rows; `make="Porsche"`, `model="Taycan"`, `model_type="4S"`, `postcode="KT7 0YJ"` (with a space); `ad_key` from the href; `detail_url` has the query stripped; `price_gbp == 42000`; `year == 2021`; `miles == 12345`; the extra column is 100; `extras_total == 100`; `extras_other == "Heated Steering"`; `save_listings` called exactly once for the page. | `db.save_listings(rows)` call in `get_total_pages`; injection of `make`, `model_type`(=`trim`) and `postcode` into each `ListingRow`. |
| W2 | core | 2 (follow-up) | `test_second_scrape_run_resights_via_get_total_pages` | seam | As W1; run 1 with `clock=T0`, run 2 with `clock=T1` and changed HTML | Run 2 changes card A's price and adds card C, and drops card B. Result: 3 rows. A: `first_found == T0`, `last_found == T1`, new price, `days_live == (T1-T0).days`. B: `last_found == T0`. C: `first_found == T1`. | The upsert update branch reached via the wiring; `ad_key` derived from the cleaned href (not the title). |
| W3 | core | 4 (run continues) | `test_db_outage_on_one_page_does_not_stop_next_page` | seam | As W1 with `Page 1 of 2` (different cards per `page=`); `fail_on(r"^\s*(SELECT\|INSERT\|UPDATE)", times=MAX_DB_ATTEMPTS)`; `chdir(tmp_path)` | No exception; page 1 rows absent; page 2 rows present; `sleeps == [1.0, 2.0, 4.0]`; error log has a line. | Break after a failed page: stop continuing to the next page. (Scenario 4's "returns without raising" is proven by R2.) |
| W4 | edge | - | `test_non_retryable_db_error_handled_by_page_handler` | seam | As W3 with `fail_on(INSERT, times=1, exc=IntegrityError)`; `chdir(tmp_path)` | Page 1 is logged via `log.exception`; `tmp_path/error_log.txt` gets a line via `db.append_error_log`; page 2 rows are saved. | n/a |
| W5 | edge | - | `test_card_without_href_is_saved_with_fallback_key` | seam | As W1, one card with no `href` | 1 row with `ad_key` starting `nokey_`; `detail_url` NULL; WARNING. | n/a |
| W6 | edge | - | `test_get_total_pages_signature` | static | `inspect.signature` | Params are exactly `postcode, make, model, trim, fuel, year_from, year_to, radius, price_to, page_start` (no `output_file`). | n/a |
| **`tests/test_db_init.py`** | | | | | | | |
| I1 | core | 8 | `test_db_init_creates_full_schema` | component | `test_db` (empty) | `run(test_db) == 0`; live columns in order equal `FIXED_COLUMNS + EXTRA_COLUMN_MAP.values()`; `sync_schema` on a fresh db gives `report.created_table`; extras are `notnull=1` with default `0` (PRAGMA); a duplicate `ad_key` insert raises `IntegrityError`; `first_found` declared type `TIMESTAMPTZ`. | `create_tables([Listing], ...)` in `sync_schema`; dynamic extra fields from `EXTRA_COLUMN_MAP`. |
| I2 | core | 9 | `test_db_init_is_idempotent` | component | `schema_db` with 2 rows; `executed` mark | Second run: `0`; `added_columns == []`; no `CREATE`/`ALTER`/`DROP` in `executed` after the mark; `SELECT * ORDER BY id` and the column list are identical. | `table_exists` guard before `create_tables` (D14). |
| I3 | core | 10 | `test_db_init_adds_only_missing_extra_column` | component | `schema_db` with rows; another extra set non-zero; `DROP COLUMN <extra>` | `added_columns == [col]`; existing rows hold `0` in it; the other non-zero extra and fixed data unchanged; second run gives `added_columns == []`. | `ALTER TABLE ... ADD COLUMN ... INTEGER NOT NULL DEFAULT 0` loop. |
| I4 | core | 11 | `test_db_init_rolls_back_on_failure_after_first_alter` | component | drop 2 extras; `fail_on(r"^\s*ALTER TABLE", times=1, skip=1)` | `run != 0` and no raise; column set equals the pre-run set (both still missing); `clear_faults()` then `run == 0` with both present. | `db.atomic()` around `sync_schema`. |
| I5 | edge | - | `test_db_init_warns_on_orphan_column_and_keeps_it` | component | `ADD COLUMN legacy_extra INTEGER` | `0`; WARNING names `legacy_extra`; column and its data still present; `report.orphan_columns == ["legacy_extra"]`. | n/a |
| I6 | edge | - | `test_db_init_skips_advisory_lock_on_sqlite` | component | `executed` | No `pg_advisory` in `executed`. | n/a |
| I7 | edge | - | `test_db_init_fails_on_missing_fixed_column` | component | raw `CREATE TABLE listing` without `postcode` | `run == 1`; the message names `postcode`; no `ALTER` in `executed` (D13). | n/a |
| **`tests/test_check_schema.py`** | | | | | | | |
| S1 | core | 12 | `test_init_db_fails_when_table_missing` | component | `test_db` (empty) | `init_db(test_db)` raises `SchemaError` containing `` `just db-init` ``; no `CREATE`/`ALTER`/`DROP` in `executed`; the table is still absent. | Break the column diff in `check_schema`, or add `create_tables` to `init_db`. |
| S2 | core | 12 | `test_init_db_fails_when_extra_column_missing` | component | `schema_db`, then `DROP COLUMN <extra>` | `SchemaError` names the column and `just db-init`; no DDL in `executed`; the column is still missing. | Column diff in `check_schema`. |
| S3 | edge | - | `test_init_db_allows_orphan_column` | component | `schema_db` with an extra live column | `init_db` succeeds. | n/a |
| S4 | edge | - | `test_init_db_fails_when_fixed_column_missing` | component | raw table without `last_found` | `SchemaError` names `last_found`. | n/a |

## Scenario index

1: U1. 2: U2, W2. 3: R1. 4: R2, W3. 5: C1, C2, C3. 6: N1, N2. 7: U3. 8: I1. 9: I2. 10: I3.
11: I4. 12: S1, S2, C3. Scraper wiring (`get_config`/`get_total_pages` to `save_listings`,
`init_db` before the browser, xlsx/pandas/openpyxl gone): W1, W2, W3, N1, N2, C3, C8.

## Final scenario to test map (phase 0002 close-out)

Every function named below exists in `tests/` and appears in `.pytest_cache/v/cache/nodeids`
(102 node ids). A fresh `uv run pytest -q` run reported `102 passed`, so every test below passes.

| Scen. | Tests (`file::function`) |
| ----- | ------------------------ |
| 1 | `test_upsert.py::test_insert_on_first_sight` |
| 2 | `test_upsert.py::test_resight_updates_price_and_preserves_first_found`; `test_scraper_wiring.py::test_second_scrape_run_resights_via_get_total_pages` |
| 3 | `test_retry.py::test_retry_then_succeed[OperationalError,InterfaceError]` |
| 4 | `test_retry.py::test_retry_exhausted_logs_error_and_continues`; `test_scraper_wiring.py::test_db_outage_on_one_page_does_not_stop_next_page` |
| 5 | `test_config.py::test_missing_database_url_fails_fast[unset,empty,whitespace]`, `::test_invalid_database_url_does_not_leak_value[wrong-scheme,bare-string]`, `::test_main_fails_before_launching_browser[unset]` |
| 6 | `test_no_spreadsheet.py::test_import_does_not_load_pandas_or_openpyxl`, `::test_get_config_run_writes_db_not_xlsx` |
| 7 | `test_upsert.py::test_ad_key_fallback_when_url_has_no_id[4 params]`; `test_scraper_wiring.py::test_card_without_href_is_saved_with_fallback_key` |
| 8 | `test_db_init.py::test_db_init_creates_full_schema` |
| 9 | `test_db_init.py::test_db_init_is_idempotent` |
| 10 | `test_db_init.py::test_db_init_adds_only_missing_extra_column` |
| 11 | `test_db_init.py::test_db_init_rolls_back_on_failure_after_first_alter` |
| 12 | `test_check_schema.py::test_init_db_fails_when_table_missing`, `::test_init_db_fails_when_extra_column_missing`; `test_config.py::test_main_fails_before_launching_browser[empty-sqlite]` |

## Notes for the test-writer and the planner

- **W rows require that `auto_trader` calls `db.save_listings` through the module attribute**
  (`import db` and then `db.save_listings(rows)`, as plan 1.5 says), so that the test can wrap it
  to inject `clock`, `sleep` and `error_log_path`. A `from db import save_listings` breaks W1-W4
  by design.
- `tests/scraper_fakes.py` holds `FakeDriver` (`get`, `page_source` keyed by the `page=` query of
  the last URL, `current_url`), a `WebDriverWait` stub whose `until()` returns, and a
  `listing_card_html(...)` builder. It is owned by the wiring writer only.
- The N4 BOM case is a small requirement the plan does not state (`read_config` must open with
  `encoding="utf-8-sig"`). It keeps parity with `pd.read_csv`. If the planner rejects it, drop
  only the BOM param.
- No conftest additions beyond plan section 1.6.
- **Not covered by CI (by design, see TECH_DEBT):** the Postgres advisory lock, `timestamptz` on
  real Postgres, the pooler and `sslmode`, and a live Firefox scrape. These are checked in
  `verification.md` Parts 2 and 3 (human-run).

## Parallel test-writer groups

Prerequisite (sequential, one writer): `tests/__init__.py` and `tests/conftest.py` (plan section 1.6).

Then, in parallel, one writer per group:

| Group | Files |
| ----- | ----- |
| G1 db core | `tests/test_helpers.py`, `tests/test_upsert.py`, `tests/test_retry.py` |
| G2 config and spreadsheet removal | `tests/test_config.py`, `tests/test_no_spreadsheet.py` |
| G3 schema | `tests/test_db_init.py`, `tests/test_check_schema.py` |
| G4 scraper wiring | `tests/scraper_fakes.py`, `tests/test_scraper_wiring.py` |
