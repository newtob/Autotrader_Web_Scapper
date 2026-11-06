# Spec 0004 — Rust read-only prices API

## Overview

`GET /api/prices` is a read-only Vercel Function written in Rust. It sits between the browser and
the Supabase Postgres `daily_stats` table (written daily by the Python scraper, Spec 0003) and
returns the daily average price series for one make / model / type over the last N days. It
supersedes the Python `api/stats.py` read API in Spec 0003 §6.

## Interface

`GET /api/prices?make=Porsche&model=Taycan&type=4S&days=30`

| Param   | Required | Rules                                                          |
| ------- | -------- | -------------------------------------------------------------- |
| `make`  | yes      | trimmed, ≤ 64 chars, no control characters                     |
| `model` | yes      | as above                                                       |
| `type`  | no       | as above; absent/empty means no trim (`''` in `daily_stats`)   |
| `days`  | yes      | integer 1–1095                                                 |

- Matching is case-insensitive.
- The window is the last `days` calendar days **ending at that search's latest priced
  `stat_date`** (same anchoring as `web/src/lib/range.ts`), not the server clock. Days with a NULL
  price are omitted.
- `200`: `{"make","model","type","days","series":[{"date":"2026-11-04","avg_price_gbp":44820}]}`,
  date ascending. An unknown search returns `"series": []`.
- `400` `{"error": "..."}` invalid or missing parameter; `405` non-GET; `503`
  `{"error":"data unavailable"}` on any database failure (no driver or connection text).
- Success: `Cache-Control: public, s-maxage=3600, stale-while-revalidate=86400`; errors: `no-store`.
- Public, no auth: it exposes only aggregate prices already shown on the page.

## Layout

```
Cargo.toml / rust-toolchain.toml   # Vercel requires Cargo.toml at the repo root
api/prices.rs                      # thin handler (vercel_runtime)
api-rs/src/{params,db,response}.rs # parsing, the single SELECT, JSON shape
api-rs/tests/db_integration.rs     # needs TEST_DATABASE_URL (throwaway Postgres); skipped otherwise
vercel.json                        # maxDuration for api/prices.rs
docs/specs/0004-Rust-Prices-API/read_only_role.sql
```

## Read-only guarantees

1. The API connects as the `prices_reader` role (`read_only_role.sql`): `SELECT` on `daily_stats`
   only, `default_transaction_read_only = on`, nothing on `listing`.
2. The query runs inside `SET TRANSACTION READ ONLY` and is always rolled back.
3. The crate contains exactly one SQL statement apart from that `SET`; there is no write code.

## Connection

`DATABASE_URL_READONLY` is the Supabase **transaction pooler** URL (port 6543, `sslmode=require`)
for `prices_reader`. The statement cache is disabled because the transaction pooler does not keep
named prepared statements. One lazily-created connection per warm instance, 3 s acquire timeout.

## Testing

`just rust-check` runs `cargo fmt --check`, clippy (`-D warnings`) and `cargo test`. Parameter and
response tests are pure. `db_integration` runs against a throwaway Postgres when
`TEST_DATABASE_URL` is set (e.g. a local `postgres:17-alpine` container); it covers window
anchoring, NULL days, case-insensitivity, type scoping, unknown searches and read-only enforcement.

## Out of scope

Write paths, auth or rate limiting beyond the CDN cache, and switching `web/` from `/api/stats`
to per-model `/api/prices` (the UI needs a model list; decide separately).
