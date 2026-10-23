# TECH_DEBT.md

Known constraints and deferred work. Ratings are low, medium or high.

## Phase 0002 (Database and ORM)

| Item | Rating | Notes |
| ---- | ------ | ----- |
| `just db-init` is additive-only | medium | Renamed or removed extras, retyped columns and drops need a manual `ALTER TABLE`. Removed extras leave orphan columns (logged as WARNING). |
| `just db-init` runs locally only | medium | Running it as a deploy step (Vercel build or CI, with per-environment `DATABASE_URL` so previews never touch production) is deferred to 0003. It uses the pooler URL; a direct/session connection is a possible follow-up. |
| snake_case column collisions are not de-duplicated | low | `build_extra_column_map()` raises `ValueError` at import on a collision (loud, not silent). Revisit if it ever fires. |
| Local file logging (`scraper.log`, `error_log.txt`) | medium | Contradicts [[logging_conventions]] (stdout JSON). Retained for the local phase; must move to structured stdout for the Vercel Function in 0003. |
| Best-effort `price_gbp` / `year` / `miles` parsing | low | Malformed values become NULL, or the row is skipped when title or price is unusable. |
| Custom `UtcDateTimeField` stores ISO strings (D6) | low | Keeps `timestamptz` on Postgres and tz-aware round trips on SQLite. Replace with native support if Peewee gains it. |
| Fixed-column drift is not auto-migrated (D13) | low | If a live table lacks a fixed column, `db-init` exits non-zero naming it. Fixed `NOT NULL` columns have no safe default. |
| No Postgres integration test in CI | medium | Tests are SQLite only. Supabase is checked by the human-run steps in `verification.md`. |
| `sqlite:///` scheme accepted for `DATABASE_URL` (D5) | low | Intended for local development and UAT only. |
| One connection per run, no in-process pool | low | Relies on the Supabase pooler; matches the 0003 serverless model. |
| Context7 unavailable during implementation | low | Peewee/dotenv APIs were verified empirically; the Supabase pooler URI shape and pooler behaviour still need confirming in `verification.md` Part 3. |
| Pooler and `psycopg2` paths unverified until `verification.md` Part 3 | high | Real Supabase transaction-pooler connectivity, `sslmode=require` negotiation, `psycopg2` timestamptz round trips and `pg_advisory_xact_lock` through the pooler have not been exercised. Part 2 (real scrape to SQLite) and Part 3 (real Supabase) are human-run and not yet done. |
| Postgres-only code paths have no automated coverage | high | The advisory-lock branch in `db_init` and Postgres DDL (`timestamptz`, `text`) only run against Supabase. Treat 0002 as unproven on Postgres until Part 3 passes. |
| Plan deviations from the design (plan.md D1-D20) | low | Worth revisiting if the design is re-read literally: D4 (`init_db` runs in `main()` before Firefox, not under `get_config`), D5 (`sqlite:///` accepted), D9 (collision raises at import), D13/D14 (no auto-migration of fixed columns; no CREATE re-run), D15 (raw `ALTER TABLE ... ADD COLUMN`, not `playhouse.migrate`), D16 (retry sleeps 1/2/4 s, whole page per transaction), D18 (`uat: guide` frontmatter added), D20 (ruff `line-length = 150`; `AGENTS.md` previously said 100). |
