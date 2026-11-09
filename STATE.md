# STATE.md

This file describes the current state of the implementation.

| Phase | Name                                                                       | Status             |
| ----- | -------------------------------------------------------------------------- | ------------------ |
| 0001  | Scraping_Vehicle_extras                                                    | done               |
| 0002  | DB and ORM                                                                 | implemented — awaiting Supabase UAT |
| 0003  | Vercel Function and Price Dashboard (UI: dark gallery, per-model price graph) | spec updated — web UI in progress; `daily_stats` table, backfill (`just db-backfill`) and per-scrape stats implemented |
| 0004  | Rust read-only prices API (`GET /api/prices`) | implemented — migrated to Axum 0.8 (single `api/index.rs` function, `/api/*` rewrite, `AppError`, router tests), request IDs and JSON request logs, generous per-client rate limit (429) and in-flight cap (503), function pinned to `lhr1` beside Supabase with the DB pool warmed at startup; fmt, clippy, tests, machete and deny pass locally; DB-backed tests run in CI only; `/api/*` rewrite not yet verified on Vercel/Supabase |
| 0005  | Multi-vehicle search (validated `make.json`, `GET /api/searches`, per-vehicle UI cards) | implemented — tests pass locally; not yet run on Vercel/Supabase |
