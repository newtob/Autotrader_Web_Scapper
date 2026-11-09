# Spec 0005 — Multi-vehicle search

## Overview

Any number of vehicles can be tracked by adding entries to `make.json`. Every entry must name a make
(`uriValue`) and a `model` and carry an `aggregatedTrim` key (`""` means all trims). The scraper,
`daily_stats` and the UI then pick them up with no further code changes.

## Scraper

- `make_config.load_make_config()` is the single `make.json` parser (used by `auto_trader.get_make_data`
  and `stats.load_searches`). It runs at startup, before Firefox, and raises `MakeConfigError` (exit code 1)
  naming the entry and field for: missing/blank `uriValue` or `model`, missing `aggregatedTrim`, invalid
  `price-to`, duplicate make/model/trim (case-insensitive).
- Listings store blank model/trim as `''` (not NULL), matching `daily_stats`. `just db-init` backfills older NULLs.
  The schema stays additive-only, so no `NOT NULL` constraint is added.

## API

`GET /api/searches` (Rust, Axum route in `api-rs/src/routes.rs`) → `{"searches":[{"make","model","type","latest_date"}]}`, ordered by
make/model/type, only searches with at least one priced day. Same read-only role, `SET TRANSACTION READ ONLY`,
cache headers and error behaviour as `/api/prices`. The UI then calls `/api/prices?days=1095` once per search
(CDN-cached) and slices periods client-side.

## UI

`App.svelte` lists searches; `SearchCard.svelte` loads one vehicle's series and renders `ModelCard`, with its own
loading, error and retry state so one failing vehicle does not blank the page.

## Tests

`tests/test_make_json.py`, `api-rs` unit and `db_integration` tests, `web/src/lib/api.test.ts`.
