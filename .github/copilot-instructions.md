# Copilot Instructions

## Tooling

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency management and [`just`](https://just.systems/) as a command runner.

| Command         | Description                                                |
|-----------------|------------------------------------------------------------|
| `just sync`     | Install / sync dependencies                                |
| `just run`      | Run the scraper (requires Firefox + geckodriver on PATH + `DATABASE_URL`) |
| `just db-init`  | Create / sync the database schema (idempotent, additive-only; requires `DATABASE_URL`) |
| `just test`     | Run the pytest suite (SQLite, hermetic)                    |
| `just check`    | Run all static-analysis checks (lint + fmt + typecheck)    |
| `just lint`     | Ruff lint (report only)                                    |
| `just fix`      | Auto-fix lint issues and format with Ruff                  |
| `just typecheck`| Type-check with `ty`                                       |
| `just pysentry` | Dependency security scan with `pysentry-rs`                |
| `just clean`    | Remove `error_log.txt` and `scraper.log`               |
| `just update`   | Upgrade all dependencies                                   |

Dependencies are declared in `pyproject.toml`; the lockfile is `uv.lock`. Requires Python ≥ 3.11.
Dev dependencies (`ruff`, `ty`) are in `[dependency-groups] dev` and installed automatically by `uv sync`.

**Ruff config** (`pyproject.toml`): `line-length = 150`, rule sets `E`, `F`, `W`, `I`.
**ty** resolves types against `.venv`.

> Note: The README lists `requests` as a dependency but it is not used. The runtime requirements are `selenium`, `beautifulsoup4`, `peewee`, `psycopg2-binary` and `python-dotenv`. `pytest` is a dev dependency.

## Architecture

The scraper lives in `auto_trader.py`. Persistence lives in `db.py` (Peewee handle, `Listing` model, `save_listings` upsert with retry) and `db_init.py` (`just db-init` schema provisioning). `main()` calls `_configure_logging()`, `load_dotenv()`, then `db.init_db()` (fails fast, before Firefox, on a missing `DATABASE_URL` or stale schema), `_load_extras_prices()`, then creates the global Firefox `driver` and `WebDriverWait` and calls `get_config()`. `db.close_db()` runs in an outer `finally`.

## SDLC

Before relying on a design document, read [`STATE.md`](STATE.md). Phase designs
describe intended behaviour; `STATE.md`, the source, and tests describe what is
actually wired. Read [`TECH_DEBT.md`](TECH_DEBT.md) for known constraints and
deferred work. Do not silently reintroduce deferred architecture.

### Working rules

1. Preserve trust and authority boundaries. A capability is not permission:
   consequential actions need scoped, auditable, and recoverable approval.
2. For user-facing, streaming, delegation, or reconvene changes, trace the
   complete path and add a deterministic regression test through the real seam.
3. Keep changes proportional to the current phase. Record deliberate deferrals
   in `TECH_DEBT.md`; update `STATE.md` only for real implementation-state
   changes.
4. Do not commit unless the user explicitly asks. Preserve unrelated work in a
   dirty worktree.

## Data flow:
1. `_load_extras_prices()` — delegates to `db.load_extras_prices()` to load `extras_prices.json` into the module-level `EXTRAS_PRICES: dict[str, dict[str, int]]` and `EXTRA_COLUMNS: list[str]` constants at startup.
2. `get_config(autotrader_config.csv)` — `read_config()` (stdlib `csv`, UTF-8 BOM tolerant) reads search parameters (postcode, start page, year range, radius), then iterates every make from `make.json`.
3. `get_total_pages()` — navigates to autotrader.co.uk, determines total pages (capped at 101), then loops pages scraping listings in **two passes**:
   - **Pass 1** (search page): `get_title()`, `get_price()`, `get_other_car_data()` parse the BeautifulSoup tree for each listing `<li>`. The detail URL is extracted from the title anchor `href`.
   - **Pass 2** (detail pages): for each listing, `get_extras(detail_url)` navigates to the individual car page, clicks "View all specs and features", expands the "Added extras" accordion, and matches the extras text against `EXTRAS_PRICES`.
4. `db.save_listings(rows)` — called once per page with snake_case `ListingRow` dicts. Upserts on `ad_key` (the 15-digit advert id from the detail URL, else a `nokey_<sha1>` fallback) inside one transaction, retrying connection errors (`MAX_DB_ATTEMPTS = 4`, exponential backoff). Rows with no title or an unparseable price are skipped.

**Output:** rows in the `listing` table of the database named by `DATABASE_URL`. No spreadsheet is produced.
**Errors:** Appended to `error_log.txt` via `db.append_error_log` (page-level exceptions are caught and logged; execution continues).
**Structured log:** `scraper.log` — DEBUG level (full selector traces, HTML snippets, row counts). Console shows INFO+.

## Data Layout

A single database table, `listing` (Peewee model `db.Listing`), with snake_case columns.

| Columns | Description |
|---|---|
| `id`, `ad_key` | Surrogate key and the unique upsert identity (15-digit AutoTrader advert id, or `nokey_<sha1>`). |
| `title`, `price_gbp`, `year`, `miles`, `fuel_type`, `detail_url` | Search-page data. |
| `make`, `model`, `model_type`, `postcode` | Search context from `make.json` and `autotrader_config.csv`. |
| `first_found`, `last_found`, `days_live` | `timestamptz` first/last sighting (UTC) and whole days between them. `first_found` is never overwritten. |
| 53 extras columns for the Porsche Taycan in 2026 (e.g. `panoramic_roof_fixed_glass`) | GBP list price when the extra is present on the car; `0` when absent. Names are the snake_case form of the keys in `extras_prices.json`. |
| `extras_total` | Integer sum of all extra-column values for that row. |
| `extras_other` | Comma-separated extras found on the car but not matched to the price table. |

## Key Files

- **`extras_prices.json`** — 53 canonical extra name → integer GBP list price entries (Porsche Taycan, 2026). Keys are snake_cased into database column names (`db.EXTRA_COLUMN_MAP`); run `just db-init` after changing them. Add or rename entries here to change what is tracked; no code changes required.
- **`make.json`** — drives which cars are searched. Each entry: `{"displayName", "uriValue", "model", "fuelType", "aggregatedTrim"}`. Adding new searches = adding entries here.
- **`db.py`** — Peewee `database` proxy, `Listing` model, `check_schema()`, `save_listings()`.
- **`db_init.py`** — additive-only schema sync run by `just db-init`.
- **`.env.template`** — documents `DATABASE_URL` (copy to `.env`; never commit real values).
- **`autotrader_config.csv`** — search location/date range. Columns: `PostalCode`, `PageNumber` (resumable start page), `year-from`, `year-to`, `radius`.
- **`specs/extras.md`** — implementation plan for the extras feature.

## Key Conventions

- **Global driver/wait**: `driver: webdriver.Firefox` and `wait: WebDriverWait` are module-level declarations (with `# type: ignore[assignment]`) set in `main()` and used directly inside functions — not passed as arguments.
- **EXTRAS_PRICES / EXTRA_COLUMNS**: module-level dicts/lists populated by `_load_extras_prices()` in `main()` (the `EXTRA_COLUMNS` gate decides whether `get_extras` runs). Functions read them as globals — no need to pass them as arguments.
- **make.json iteration order**: Makes are loaded and reversed (`[::-1]`) before iteration. Preserve this when modifying make loading.
- **Pagination cap**: Total pages are capped at `min(101, actual_pages)` — AutoTrader limits search results to 100 pages.
- **Listing selector**: `soup.select('a[data-testid="search-listing-title"]')` — finds only fully-loaded cards. The page is fully scrolled before parsing via `scroll_to_load_all()`. A 2 s sleep after the explicit wait gives React time to hydrate the infinite-scroll container before the first height measurement.
- **Two-pass scraping**: Pass 1 collects all search-page data into `raw_listings` (snake_case `ListingRow` dicts including `detail_url`). Pass 2 reads `detail_url` from each row and visits the detail page. The browser is on the last detail page after Pass 2; the outer loop navigates directly to the next search page URL.
- **Price selector**: `[data-testid*="price"]` (preferred, stable) with a fallback to any text node matching `£\d+`. The old auto-generated CSS class `span.at__sc-1mc7cl3-5.edXwbj` is gone.
- **Extras matching**: `_normalise()` lowercases and strips punctuation; `_match_extra()` tries exact then keyword match (all words ≥ 4 chars from canonical name must appear in the extra text).
- **Detail page navigation**: `get_extras()` uses `WebDriverWait(driver, 60)` for page load, then tries multiple `data-testid` selectors and XPath text searches to click "View all specs and features" and "Added extras". If either click fails, it returns `{}` and logs at DEBUG.
- **Cookie consent**: `setup_cookies()` sets `acceptATCookies=true` and `atwv=1` on `.autotrader.co.uk` before any search. Without these the Sourcepoint CMP blocks listings from rendering.
- **Page load strategy**: `eager` (fires on DOMContentLoaded, not `load`) — prevents hanging on AutoTrader's ad/tracking resources.

- **Dedup**: the `ad_key` upsert replaces the old `[Title, Price, Year, Miles, Fuel_Type]` spreadsheet dedup.
- **Config**: `DATABASE_URL` comes from the environment only; `load_dotenv()` runs in entrypoints, never in `db.py`. Never log or print its value.
- **sslmode**: Postgres URLs that do not set `sslmode` get `sslmode=require` appended by `db.with_default_sslmode()`; an explicit value is kept. `sqlite:///` URLs are accepted for local testing only.
- **Schema**: the scraper never runs DDL. `db.init_db()` only checks; `just db-init` provisions.

## Test layout

`tests/` is a hermetic pytest suite (`just test`): file-backed SQLite under `tmp_path`, no network, no Firefox, no `DATABASE_URL`. `conftest.py` and `scraper_fakes.py` hold shared fixtures and fakes. Files: `test_config.py` (URL/scheme/sslmode), `test_check_schema.py`, `test_db_init.py`, `test_upsert.py`, `test_retry.py`, `test_helpers.py`, `test_scraper_wiring.py` and `test_no_spreadsheet.py`. Postgres-only paths (pooler, `psycopg2`, advisory lock) are not covered by automated tests; see `TECH_DEBT.md`.

## AgentSoul Memory Policy

Location: `@/Users/bennewton/Ai/AgentSoul/AgentSoul/`

- Store memories, conventions, and major architectural choices.
- AgentSoul is its own repository; update its files directly without committing as part of this repository's work
- Use OKF markdown format with frontmatter that includes `type:`
- Follow the `llm-wiki` pattern for pages and links
- Before every /plan, search AgentSoul for relevant prior knowledge, conventions, architectural decisions, and known gaps
- Update AgentSoul after every major architecture, contract, CI/CD, security, operational, or incident-driven change
- Dependency upgrades and documentation-only changes are not major unless they alter an established convention or contract
- The purpose of AgentSoul is to capture this repository's way of working so future projects can reuse proven knowledge and practices
