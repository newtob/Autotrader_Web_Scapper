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
api/index.rs                       # the one Vercel function: Axum router wrapped in VercelLayer
api-rs/src/routes.rs               # Axum router, handlers, trace + timeout layers
api-rs/src/{error,state,telemetry}.rs # AppError (IntoResponse), pool state, JSON tracing setup
api-rs/src/limits.rs               # per-client rate limit and in-flight cap
api-rs/src/{params,db,response}.rs # parsing, the single SELECT, JSON shape
api-rs/tests/http.rs               # router tests via tower `oneshot` (no sockets)
api-rs/tests/db_integration.rs     # needs TEST_DATABASE_URL (throwaway Postgres); skipped otherwise
vercel.json                        # maxDuration for api/index.rs; rewrites /api/* to it
docs/specs/0004-Rust-Prices-API/read_only_role.sql
```

## Framework

Axum 0.8 on `vercel_runtime` (its `axum` feature, `VercelLayer`). `vercel.json` rewrites every
`/api/*` request to the single `api/index.rs` function, and the router dispatches by path. All
failures go through `AppError`, which renders the fixed JSON error bodies above. A `TimeoutLayer`
(8 s, under the 10 s `maxDuration`) answers `503` with an empty body if a request hangs. Path
parameters, if ever added, use the Axum 0.8 `{id}` syntax. `HEAD` is answered by the `GET` handler
(Axum default); other methods return `405`.

### Request IDs and logs

Every response carries `x-request-id`: a UUID, or the caller's own value if they sent one (it is
not overwritten, so treat it as a correlation hint, not a trusted identity). Logs are JSON, one
object per line, at `LOG_LEVEL` (default `info`). Each request logs one line with `method`,
`path`, `request_id`, `status` and `latency` (ms). The query string is not logged. Errors log by
kind only, never driver or connection text.

### Abuse limits (`api-rs/src/limits.rs`)

Both are generous, so ordinary use never sees them, and both are **per warm instance**, not
global. The CDN cache absorbs repeat requests; firm platform-wide limits belong in Vercel's firewall.

| Limit | Default | Over the limit |
| ----- | ------- | -------------- |
| Per client (`x-real-ip`, else first `x-forwarded-for`; no header shares one bucket) | burst 120, refills 10 a second | `429` `{"error":"too many requests"}` + `Retry-After` |
| Requests in flight, all clients | 64 | `503` `{"error":"server busy"}` + `Retry-After: 1` |

The rate limit sits outside the in-flight cap, so a throttled client never takes a slot. The
client key relies on Vercel overwriting those headers; behind any other host it can be spoofed.

## Read-only guarantees

1. The API connects as the `prices_reader` role (`read_only_role.sql`): `SELECT` on `daily_stats`
   only, `default_transaction_read_only = on`, nothing on `listing`.
2. The query runs inside `SET TRANSACTION READ ONLY` and is always rolled back.
3. The crate contains exactly two SQL statements apart from that `SET` (the price series and, since Spec 0005, the distinct-searches list), both `SELECT`; there is no write code.

## Connection

`DATABASE_URL_READONLY` is the Supabase **transaction pooler** URL (port 6543, `sslmode=require`)
for `prices_reader`. The statement cache is disabled because the transaction pooler does not keep
named prepared statements. One lazily-created connection per warm instance, 3 s acquire timeout.

## Testing

`just rust-check` runs `cargo fmt --check`, clippy (`-D warnings`) and `cargo test`. Parameter and
response tests are pure. `http` drives the router directly (400, 405, 503 without a pool, 404, and
the 200 path and cache headers when `TEST_DATABASE_URL` is set). `db_integration` runs against a throwaway Postgres when
`TEST_DATABASE_URL` is set (e.g. a local `postgres:17-alpine` container); it covers window
anchoring, NULL days, case-insensitivity, type scoping, unknown searches and read-only enforcement.

## Out of scope

Write paths and auth or rate limiting beyond the CDN cache. The UI model list is `GET /api/searches` (Spec 0005).
