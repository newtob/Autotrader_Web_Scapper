# AutoTrader UK Web Scraper

A scraper for [autotrader.co.uk](https://www.autotrader.co.uk) that searches for car listingsand extracts details by viewing each car's individual advert page. It also collects optional extras, and stores results in a Postgres database (Supabase) through the Peewee ORM.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](http://creativecommons.org/licenses/by-nc/4.0/)

---

## Features

- Configurable searches by postcode, year range, radius, make, model, trim, fuel type, and maximum price
- Infinite-scroll page loading — all listings on each results page are fully loaded before parsing
- Per-listing detail-page scraping — visits each advert to extract the "Added extras" section
- Extras matched against a per-model price table (`extras_prices.json`) and totalled in a dedicated column (53 extras for the Porsche Taycan, 2026; the database columns are derived from `extras_prices.json`)
- Structured logging to console (`INFO+`) and `scraper.log` (`DEBUG`)
- Results are upserted into a `listing` table keyed on the AutoTrader advert id, tracking `first_found`, `last_found` and `days_live`

---

## Prerequisites

| Tool | Purpose | Install |
|---|---|---|
| [Python ≥ 3.11](https://www.python.org/downloads/) | Runtime | system / pyenv |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Dependency & venv management | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Firefox](https://www.mozilla.org/en-GB/firefox/) | Browser for Selenium | system |
| [geckodriver](https://github.com/mozilla/geckodriver/releases) | Firefox WebDriver bridge | must be on `PATH` |
| [just](https://just.systems/man/en/packages.html) *(optional)* | Command runner | `brew install just` / `cargo install just` |
| [prek](https://prek.j178.dev) *(optional)* | Git pre-commit hooks | `cargo install prek` |
| [betterleaks](https://github.com/crnvl96/betterleaks) *(optional)* | Secret scanning in pre-commit | `cargo install betterleaks` |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/Ben-Newton_syne/Autotrader_Web_Scapper.git
cd Autotrader_Web_Scapper

# 2. Install Python dependencies (creates .venv automatically)
just sync

# 3. Install git pre-commit hooks
prek install
```

> `just sync` runs `uv sync` which reads `pyproject.toml`, resolves the lockfile (`uv.lock`), and creates `.venv` — no manual `pip install` required.

---

## Configuration

**Two files must be edited before the first run.** A third controls extras pricing.

### `autotrader_config.csv`

Defines the postal search area and year filter. Each row triggers a separate search iteration.

```csv
PostalCode,PageNumber,year-from,year-to,radius
KT70YJ,1,2020,2022,100
```

| Column | Description |
|---|---|
| `PostalCode` | UK postcode used as the search centre (no spaces) |
| `PageNumber` | Results page to start from — use `1` to start from the beginning |
| `year-from` | Minimum registration year |
| `year-to` | Maximum registration year |
| `radius` | Search radius in miles |

### `make.json`

Defines which make/model combinations to scrape. Each entry produces an independent search. The list is processed in reverse order.

```json
[
    {
        "displayName": "Porsche",
        "uriValue": "Porsche",
        "model": "Taycan",
        "fuelType": "Electric",
        "aggregatedTrim": "4S",
        "price-to": 50000
    }
]
```

| Field | Required | Description |
|---|---|---|
| `uriValue` | ✅ | Make name as used in the AutoTrader URL (e.g. `"Porsche"`) |
| `model` | ✅ | Model name as used in the AutoTrader URL (e.g. `"Taycan"`) |
| `fuelType` | ✅ | Fuel type filter (e.g. `"Electric"`, `"Petrol"`, `"Diesel"`) |
| `aggregatedTrim` | optional | Trim variant (e.g. `"4S"`) — omit to match all trims |
| `price-to` | optional | Maximum asking price in GBP — omit for no upper limit |
| `displayName` | optional | Human-readable label used in log output only |

To search for multiple cars, append additional objects to the array:

```json
[
    { "uriValue": "Porsche", "model": "Taycan",   "fuelType": "Electric", "aggregatedTrim": "4S", "price-to": 50000 },
    { "uriValue": "Hyundai", "model": "Ioniq 5",  "fuelType": "Electric" }
]
```

### `extras_prices.json`

Maps optional extras to their GBP list prices, grouped by `"{Make}_{Model}"` key. When the scraper visits a car's advert page it matches the "Added extras" section against this table. Matched items get individual columns with the list price; unmatched items are captured in `extras_other`.

```json
{
    "Porsche_Taycan": {
        "Panoramic Roof Fixed Glass": 1137,
        "Head-Up Display": 1128,
        "Burmester High-End 3D Surround Sound System": 4200
    },
    "Hyundai_Ioniq_5": {
        "Heat Pump": 1000,
        "Digital Side Mirrors": 600
    }
}
```

The key format is `"{uriValue}_{model}"` with spaces replaced by underscores. If no segment exists for a searched model the scraper continues normally — extras columns will be `0` and a warning is logged.

---

## Database setup

Results are stored in a Postgres database (Supabase transaction-mode pooler). `DATABASE_URL` is read from the environment only.

```bash
cp .env.template .env
# edit .env and set DATABASE_URL (never commit it)
just db-init
```

`just db-init` creates the `listing` table and adds any missing extras columns. It is idempotent and additive-only; run it again whenever `extras_prices.json` gains entries. The scraper itself never changes the schema and fails fast, pointing at `just db-init`, if the table is out of date. `sqlite:///local.db` is accepted for local testing only.

---

## Running

```bash
just run
```

Firefox will open automatically. The scraper logs progress to the console and writes full debug output to `scraper.log`. Listings are upserted into the database; no spreadsheet is produced.

---

## Available Commands

| Command | Description |
|---|---|
| `just sync` | Install / sync dependencies via `uv` |
| `just run` | Run the scraper (requires Firefox + geckodriver on `PATH` and `DATABASE_URL`) |
| `just db-init` | Create / sync the database schema (requires `DATABASE_URL`) |
| `just test` | Run the test suite (SQLite, no network) |
| `just check` | Run all static-analysis checks (lint + format + typecheck) |
| `just lint` | Ruff lint — report only, no changes |
| `just fix` | Auto-fix lint issues and reformat with Ruff |
| `just typecheck` | Type-check with `ty` |
| `just pysentry` | Dependency security scan with `pysentry-rs` |
| `just clean` | Remove `error_log.txt` and `scraper.log` |
| `just update` | Upgrade all dependencies to latest compatible versions |

---

## Database table: `listing`

| Column | Description |
|---|---|
| `id` | Surrogate primary key |
| `ad_key` | AutoTrader advert id (15 digits) — the unique upsert identity; `nokey_<sha1>` when the URL has none |
| `title`, `price_gbp`, `year`, `miles`, `fuel_type`, `detail_url` | Listing data from the search page |
| `make`, `model`, `model_type`, `postcode` | Search context |
| `extras_total` | Sum of matched optional extras list prices (£) |
| `extras_other` | Comma-separated extras found on the car but absent from `extras_prices.json` |
| `first_found`, `last_found` | UTC timestamps of first and latest sighting (`first_found` is never overwritten) |
| `days_live` | Whole days between `first_found` and `last_found` |
| *(one column per extra)* | snake_case of the `extras_prices.json` key; GBP list price if present on this car, otherwise `0` |

---

## Project Structure

```
Autotrader_Web_Scapper/
├── auto_trader.py          # Scraper — Selenium scraping and orchestration
├── db.py                   # Peewee model, schema check, upsert with retry
├── db_init.py              # `just db-init` schema provisioning
├── tests/                  # pytest suite (SQLite, hermetic)
├── .env.template           # Documents DATABASE_URL (copy to .env)
├── TECH_DEBT.md            # Known constraints and deferred work
├── make.json               # ✏️ Edit: search targets (make, model, fuel, trim, price ceiling)
├── extras_prices.json      # ✏️ Edit: per-model optional extras price tables
├── autotrader_config.csv   # ✏️ Edit: postcode, year range, radius, start page
├── pyproject.toml          # Python project metadata and dependencies (uv)
├── Justfile                # Task runner commands
├── prek.toml               # Pre-commit hook configuration (prek)
└── uv.lock                 # Locked dependency manifest
```

---

## Disclaimer

This tool is intended for personal research only. Use it responsibly and in accordance with [AutoTrader's terms of service](https://www.autotrader.co.uk/content/legal/terms-of-use). Do not use it for commercial data collection or in ways that place excessive load on AutoTrader's servers.
