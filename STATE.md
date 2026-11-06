# STATE.md

This file describes the current state of the implementation.

| Phase | Name                                                                       | Status             |
| ----- | -------------------------------------------------------------------------- | ------------------ |
| 0001  | Scraping_Vehicle_extras                                                    | done               |
| 0002  | DB and ORM                                                                 | implemented — awaiting Supabase UAT |
| 0003  | Vercel Function and Price Dashboard (UI: dark gallery, per-model price graph) | spec updated — web UI in progress; `daily_stats` table, backfill (`just db-backfill`) and per-scrape stats implemented |
| 0004  | Rust read-only prices API (`GET /api/prices`) | implemented — tests pass locally; not yet run on Vercel/Supabase |
