---
uat: guide
---

# Spec 0002 — Database and ORM

## Overview

Phase 0001 made the scraper write a timestamped `.xlsx` spreadsheet per run. This phase
**replaces that spreadsheet with a database table** populated through an ORM.

- **ORM:** [Peewee](https://docs.peewee-orm.com/) — a small, modern Python ORM.
- **Database:** Supabase (hosted PostgreSQL), project `AutoTrader_Scraper`
  (ID `dfanjkhgptqnrkdhwxlx`, region `eu-west-2`). The project already exists — do **not**
  create a new one.
- **Identity model:** each AutoTrader advert is a row keyed by its advert id. Re-seeing an
  advert **upserts** it (update price etc., bump `last_found`, recompute `days_live`) instead
  of appending a duplicate.
- **Serverless-first:** config comes only from environment variables, connections are
  short-lived, and there is **no local file output**. This sets up phase 0003 (Vercel
  Function running daily in the cloud) without doing the deployment work here.

All Excel/pandas output logic is deleted in this phase.

This document follows the format of `docs/specs/0001-Scraping_Vehicle_extras.md`.

---

## Technical Requirements

### 1. Dependencies

Add to `pyproject.toml` `[project].dependencies`:

- `peewee` — ORM.
- `psycopg2-binary` — PostgreSQL driver (**decided**). Approved for this project only; it is
  deliberately **not** added to `Approved_Dependencies.md`.
- `python-dotenv` — load `.env` locally only (already approved).

Add to `[dependency-groups].dev`:

- `pytest` — no test suite exists yet (already approved).

Remove:

- `openpyxl` — only used for `.xlsx` output.
- `pandas` — after this phase its only remaining use is `pd.read_csv` /`pd.read_excel`.
  Replace `pd.read_csv(autotrader_config.csv)` with the stdlib `csv` module and drop the
  dependency entirely. (Keeping pandas just to read one small CSV is disproportionate.)

### 2. Configuration (environment variables only)

| Variable       | Required | Format / notes                                                              |
| -------------- | -------- | --------------------------------------------------------------------------- |
| `DATABASE_URL` | yes      | Full Postgres connection URL to the Supabase **transaction-mode pooler**.   |

- **Local dev:** values live in `.env` (gitignored). Commit a `.env.template` per the
  [[env_file_usage]] contract with a safe placeholder and a comment describing format and
  required status. Document `cp .env.template .env`. Load with `python-dotenv` **locally
  only** (call `load_dotenv()` at startup; it is a no-op when no `.env` is present, so it is
  safe in the cloud).
- **Cloud (0003):** the same `DATABASE_URL` is injected by the Vercel env-var/secret store.
  No `.env` is shipped.
- **Fail fast:** if `DATABASE_URL` is missing/empty, raise a clear error naming the variable
  and pointing at `.env.template` — **without printing the value**. Never log connection
  strings (they contain the password); see [[logging_conventions]] "Never log".

**Supabase connection string (transaction pooler).** The transaction-mode pooler (default
port `6543`, host `...pooler.supabase.com`, user `postgres.<project-ref>`) is the correct fit
for short-lived serverless connections. Caveats to honour:

- `sslmode=require` must be set.
- The transaction pooler does **not** support session-level server-side prepared statements.
  With `psycopg2` this is generally fine; if `psycopg` (v3) is chosen instead, prepared
  statements must be disabled (`prepare_threshold=0`).

> The exact current Supabase connection-string shape and the exact Peewee connect/upsert API
> **must be re-verified against live docs via the Context7 MCP at implementation time**
> (`resolve-library-id` + `query-docs` for `supabase` and `peewee`). The Context7 tools were
> not available while writing this spec. Do not hardcode a connection string from memory, and
> never commit real credentials.

### 3. Database module (`db.py`)

A new module `db.py` owns the Peewee database handle and the model.

- Create a module-level `database` as a Peewee Postgres database connected from
  `DATABASE_URL` (via `playhouse.db_url.connect(...)`, to be confirmed against live docs).
  Use `autoconnect=False`; the run opens one connection at the start and closes it at the
  end (`database.connect()` / `database.close()`), matching the short-lived serverless model.
- A `BaseModel(Model)` binds `Meta.database = database`.
- The `Listing` model holds the fixed columns (Section 4). The 53 **extra columns are
  added dynamically** (Section 5).
- `init_db()` connects and calls `check_schema()`, which only **reads**: it fails fast with an
  actionable error ("run `just db-init`") if the `listing` table or any expected column is
  missing. The scraper never issues DDL. Schema provisioning lives in `db_init.py`
  (Section 3a).
- Tests swap the handle to an in-memory SQLite database via `database.bind(...)` /
  `Model.bind(...)` so no network or Supabase is needed (Section 8).

### 3a. Schema provisioning — `just db-init` (local only)

Tables are created and kept in sync by a small Python script, `db_init.py`, run **by hand from a
developer machine** via a new Justfile recipe. It is **not** wired into the scraper's startup,
Vercel, or CI in this phase (a deploy-time step is a 0003 decision; see Out of Scope). Python
(not bash) is used so the schema comes from the same `Listing` model and
`extras_prices.json` the scraper uses — there is no second hand-kept column list to drift.

```just
# Create / sync the database schema (idempotent; local only)
[group('db')]
db-init:
    uv run python db_init.py
```

**Behaviour — idempotent and additive-only.** Re-running with nothing to change is a no-op;
re-running after a failure or after `extras_prices.json` gains entries converges to the model:

1. Load config (`load_dotenv()` + fail-fast `DATABASE_URL`, Section 2) and connect.
2. Open **one transaction** (Postgres DDL is transactional, so any failure rolls the whole run
   back and a re-run starts clean).
3. On Postgres, take `pg_advisory_xact_lock(<fixed key>)` first, so two overlapping runs
   serialise. The transaction-scoped form is used because it also works through the
   transaction-mode pooler. Skipped on SQLite (tests).
4. `create_tables([Listing], safe=True)` — `CREATE TABLE IF NOT EXISTS`.
5. Diff the model's columns against `database.get_columns('listing')` (portable Peewee
   introspection, works on Postgres and SQLite) and `ALTER TABLE ... ADD COLUMN` each missing
   one. Missing extras are added as `integer NOT NULL DEFAULT 0`, so existing rows are valid.
6. Commit, log what was created/added (counts and column names, never the URL), close.

**Never destructive.** `db_init.py` does not drop, rename, or retype anything, and does not
delete data. Columns in the database but absent from the model (e.g. an extra removed from the
JSON) are logged at WARNING and left alone. Renames, drops and type changes remain manual
(TECH_DEBT).

**Exit codes.** `0` on success (including no-op), non-zero with an actionable message on any
failure; nothing is half-applied.

**Connection note.** It uses `DATABASE_URL` (the transaction pooler) so there is no extra
config; DDL inside a single transaction is fine there. If pooler/DDL issues appear in
practice, switching to Supabase's direct/session connection is a documented follow-up, not
built now.

### 4. Table schema — `listing`

Table and column names are **snake_case**. Types are given as Postgres / Peewee field.

| Column         | Type (PG / Peewee)                | Null?     | Notes                                                                                     |
| -------------- | --------------------------------- | --------- | ---------------------------------------------------------------------------------------- |
| `id`           | serial / `AutoField`              | NOT NULL  | Surrogate primary key.                                                                    |
| `ad_key`       | text / `CharField(unique=True)`   | NOT NULL  | Our name for AutoTrader's advert id (AutoTrader gives it no name): a 15-digit number, e.g. `202609206214030`. Upsert identity (Section 6). Unique index. |
| `title`        | text / `CharField`                | NOT NULL  | Listing title. Rows with no title are skipped (matches 0001 behaviour).                   |
| `price_gbp`    | integer / `IntegerField`          | NOT NULL  | Price in GBP. Scraped as text (e.g. `£42,000`); strip the `£` symbol and commas, store as integer. Unparseable price → row skipped. |
| `year`         | integer / `IntegerField(null)`    | NULL      | Registration year; NULL if absent/unparsed.                                              |
| `miles`        | integer / `IntegerField(null)`    | NULL      | Mileage; NULL when the card shows "NA".                                                   |
| `fuel_type`    | text / `CharField(null)`          | NULL      | e.g. `Electric`.                                                                          |
| `detail_url`   | text / `CharField(null)`          | NULL      | Full advert URL.                                                                          |
| `make`         | text / `CharField`                | NOT NULL  | From the `make.json` entry being scraped.                                                 |
| `model`        | text / `CharField(null)`          | NULL      | From `make.json`; NULL when blank.                                                        |
| `model_type`   | text / `CharField(null)`          | NULL      | `aggregatedTrim` from `make.json`.                                                        |
| `postcode`     | text / `CharField`                | NOT NULL  | Search postcode from `autotrader_config.csv`.                                             |
| `extras_total` | integer / `IntegerField(default=0)` | NOT NULL | Sum of the extra columns for this row.                                                    |
| `extras_other` | text / `CharField(null)`          | NULL      | Comma-separated extras found but not in the price table (was `Extras_Other`).             |
| `first_found`  | timestamptz / `DateTimeField`     | NOT NULL  | Set once, on first insert. Never overwritten.                                             |
| `last_found`   | timestamptz / `DateTimeField`     | NOT NULL  | Set on every insert and every re-sight.                                                   |
| `days_live`    | integer / `IntegerField(default=0)` | NOT NULL | Whole days the advert has been live: `(last_found - first_found).days`. `0` on insert.    |
| *extra columns* | integer / `IntegerField(default=0)` | NOT NULL | One per canonical extra (Section 5). GBP list price when present on the car, else `0`.   |

### 5. Dynamic extra columns (derived from `extras_prices.json`)

`extras_prices.json` is nested by `"{Make}_{Model}"` segment; `EXTRA_COLUMNS` is already the
ordered **union** of extra names across all segments. The extra columns are derived from this
at import/startup — there is **no hand-maintained list**.

> Count note: AGENTS.md / spec 0001 said "38 extras"; the count is **53** for the Porsche Taycan in 2026 (confirmed by the
> user). The columns must still be derived from the JSON at runtime, never hardcoded.

**snake_case naming rule** (applied to each canonical key):

1. Lowercase.
2. Replace every run of non-alphanumeric characters with a single `_`.
3. Strip leading/trailing `_`.
4. If the result starts with a digit, prefix `extra_` (Postgres-friendly, avoids quoted-identifier
   awkwardness).

Samples:

| Canonical key                                | Column name                                        |
| -------------------------------------------- | -------------------------------------------------- |
| `Panoramic Roof Fixed Glass`                 | `panoramic_roof_fixed_glass`                       |
| `BOSE Surround Sound System`                 | `bose_surround_sound_system`                       |
| `20-inch Taycan Turbo S Aero Design Wheels`  | `extra_20_inch_taycan_turbo_s_aero_design_wheels`  |
| `4+1 Seating System Rear 2+1 Bench`          | `extra_4_1_seating_system_rear_2_1_bench`          |
| `150kW DC On-Board Booster`                  | `extra_150kw_dc_on_board_booster`                  |

Keep a module-level `EXTRA_COLUMN_MAP: dict[str, str]` (canonical name → column name) so the
upsert can map `get_extras()` output back to fields.

**Peewee implication.** Model fields are bound at class creation by the metaclass, so the
dynamic columns must be built **before/at** class creation. Approach: build a dict of
`{column_name: IntegerField(default=0)}` from `EXTRA_COLUMN_MAP`, then create the model class
with `type("Listing", (BaseModel,), {...fixed fields..., **extra_fields})`. This runs once at
import, after `extras_prices.json` is loaded.

**Schema migration when `extras_prices.json` changes.** `create_tables(safe=True)` only
creates a *missing table*; it never adds columns. New extras are therefore added by re-running
`just db-init` (Section 3a), which diffs the model against the live table and adds the missing
`integer NOT NULL DEFAULT 0` columns. Until it has been run, the scraper's startup
`check_schema()` fails fast rather than writing to a stale table. Renames, drops and type
changes are not automated (TECH_DEBT).

### 6. Upsert / identity logic

The old dedup key `[Title, Price, Year, Miles, Fuel_Type]` is replaced by `ad_key`.

**`ad_key` extraction.** `ad_key` is our own name for AutoTrader's advert id, which AutoTrader
does not name. It is a 15-digit number (e.g. `202609206214030`) and appears in the detail URL
(`https://www.autotrader.co.uk/car-details/<15-digit-id>`). Rule: take the last non-empty path
segment of `detail_url` after stripping the query string, and accept it only if it matches
`^\d{15}$` (a regex search for `\d{15}` in the path is an acceptable equivalent). **Fallback**
when no 15-digit id is found: synthesize a deterministic key
`nokey_<sha1(title|year|miles|fuel_type)>`, log a `WARNING`, and still store the row so data is
not silently lost.

**Upsert behaviour** (per listing):

- **First sight** (no row with this `ad_key`): insert with `first_found = now`,
  `last_found = now`, `days_live = 0`.
- **Re-sight** (row exists): update `price_gbp`, `year`, `miles`, `fuel_type`, `detail_url`,
  `make`, `model`, `model_type`, `postcode`, `extras_total`, `extras_other`, and all extra
  columns; set `last_found = now`; recompute `days_live = (last_found - first_found).days`.
  **`first_found` is preserved.**

**Implementation choice: read-modify-write inside a transaction**, not a single-statement
`ON CONFLICT`. Rationale: `days_live` depends on the *existing* `first_found`, which is awkward
to reference inside one upsert statement; and a `get`-then-`update`/`insert` works identically
on SQLite (tests) and Postgres, making the behaviour deterministic and easy to test. `days_live`
is **stored** (per requirement), recomputed on each write. (Single-statement `ON CONFLICT` is a
possible later optimisation — Out of Scope.)

### 7. Resilience — retry loop around each page write

A failed page write must not kill the run.

Named constants (in `db.py`):

```
MAX_DB_ATTEMPTS      = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS  = 30.0
```

- `save_listings(rows)` wraps the page's upserts in a retry loop: attempt → on a **retryable**
  exception, close and reconnect (`database.close()`; `database.connect(reuse_if_open=True)`),
  sleep `min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2 ** (attempt-1))`, retry.
- **Retryable exceptions:** `peewee.OperationalError`, `peewee.InterfaceError` (connection
  loss / pooler drop). Other exceptions are non-retryable and handled by the existing
  page-level `try/except` in `get_total_pages`.
- **After attempts are exhausted:** log at `ERROR` (`logger.exception`), append a line to
  `error_log.txt`, **skip that page's batch, and continue the run.**

### 8. Testing

No `tests/` dir or pytest config exists yet — this phase adds them.

- Add `pytest` (dev) and a `tests/` package; add a `just test` recipe:
  `uv run pytest -q`.
- Tests bind `Listing`/`database` to an **in-memory SQLite** Peewee database — no network,
  no Supabase. The schema is built by calling the same `db_init` function that
  `just db-init` runs (including dynamic extra columns) against SQLite.
- `ty` flags dynamically-created Peewee field attributes. Use **targeted**
  `# type: ignore[unresolved-attribute]` on the specific dynamic-field accesses (or a small
  typed accessor helper) — **not** a blanket file/rule disable.

**Acceptance scenarios (Given/When/Then, through the real seam):**

1. **Insert on first sight** — *Given* an empty table, *When* a listing with `ad_key=A` is
   saved, *Then* one row exists with `first_found == last_found` and `days_live == 0`.
2. **Upsert on re-sight preserves `first_found`** — *Given* row `A` with `first_found = T0`,
   *When* the same `ad_key` is saved later at `T1` with a different price, *Then* still one
   row; `price_gbp` updated; `first_found == T0`; `last_found == T1`;
   `days_live == (T1 - T0).days`.
3. **Retry then succeed** — *Given* a DB that raises `OperationalError` on the first N-1
   attempts then succeeds, *When* a page batch is saved, *Then* the row is persisted and
   `retry_count` is logged.
4. **Retry exhausted → run continues** — *Given* a DB that always raises `OperationalError`,
   *When* a page batch is saved, *Then* `save_listings` logs an error, writes to
   `error_log.txt`, returns without raising, and the run continues.
5. **Missing config fails fast** — *Given* `DATABASE_URL` unset, *When* the app initialises,
   *Then* it raises a clear error naming the variable, and the error contains no secret value.
6. **No spreadsheet produced** — *When* a full run completes, *Then* no `*.xlsx` file is
   created and `openpyxl`/`pandas` are not imported.
7. **`ad_key` fallback** — *Given* a listing whose `detail_url` has no parseable id, *When* it
   is saved, *Then* it is stored with a `nokey_<hash>` key and a WARNING is logged.
8. **`db-init` creates the schema** — *Given* an empty database, *When* `db_init` runs, *Then*
   the `listing` table exists with every fixed column and every extra column.
9. **`db-init` is idempotent** — *Given* a populated, up-to-date database, *When* `db_init`
   runs again, *Then* it exits 0, makes no schema change, and no row is altered or lost.
10. **`db-init` adds only missing extras** — *Given* a table lacking one extra column and
    holding existing rows, *When* `db_init` runs, *Then* that column is added with default `0`
    on existing rows, other columns and data are untouched, and a second run is a no-op.
11. **`db-init` is atomic and recoverable** — *Given* a failure injected after the first
    `ALTER`, *When* `db_init` runs, *Then* it exits non-zero and the schema is unchanged
    (rolled back); *When* it is re-run without the fault, *Then* it completes successfully.
12. **Scraper never runs DDL** — *Given* a database missing the table or an expected column,
    *When* the scraper initialises, *Then* `check_schema()` raises an error naming
    `just db-init` and no DDL statement is issued.

---

## Data Flow (changes vs. 0001)

```
get_config(csv)                      # stdlib csv now, not pandas; no get_file_name()
  └─ init_db()                       # connect + read-only check_schema() (fail fast → `just db-init`)
  └─ for each config row (postcode…):
       └─ for each make/model/trim:
            └─ get_total_pages(postcode, make, model, trim, …)   # no output_file arg
                 └─ per page: build rows with extras (unchanged Pass 1 / Pass 2)
                      └─ add make, model, model_type(=trim), postcode, ad_key per row
                      └─ save_listings(rows)   # upsert batch w/ retry  ← replaces save_data()
  └─ database.close()
```

Call-site changes in `auto_trader.py`:

- `save_data(output_file, cars_data)` at ~line 590 → `save_listings(rows)` (no file path).
- `get_total_pages(...)` loses the `output_file` parameter; gains the values it already has in
  scope (`make`, `model`, `trim`→`model_type`, `postcode`) injected into each row dict.
- Rows now carry snake_case keys matching the model (or a thin mapping layer converts the
  existing `Title/Price/...` dict keys to model fields in `save_listings`).
- `get_config()` no longer calls `get_file_name()` or reads back an xlsx at the end; it logs a
  summary count via a `Listing.select().count()` instead.
- `get_file_name()` and `save_data()` (xlsx) are **deleted**.

---

## Documentation / state updates

- **AGENTS.md / README.md:** remove xlsx-output references; describe the single database table,
  `DATABASE_URL`, Peewee, and the upsert identity. Correct the stale "38 extras" to
  "53 extras (Porsche Taycan, 2026), derived from `extras_prices.json`".
- **Justfile:** add the `db-init` recipe (Section 3a); `clean` no longer removes `*.xlsx` (keep `error_log.txt`/`scraper.log`
  cleanup); add `test` recipe.
- **`.env.template`:** create, documenting `DATABASE_URL`.
- **`TECH_DEBT.md`:** does not exist — create it with the deferrals below.
- **`STATE.md`:** update 0002 to `done` **only once implemented**, not at spec time.

### `TECH_DEBT.md` entries to record

- `just db-init` is additive-only: renamed/removed extras, retyped columns and drops require a
  manual `ALTER TABLE`; removed extras leave orphan columns (logged as WARNING).
- `just db-init` runs locally only; running it as a deploy step (Vercel build step or CI
  before deploy, with per-environment `DATABASE_URL` so previews never touch production) is
  deferred to 0003. It uses the pooler URL; a direct/session connection is a possible
  follow-up.
- snake_case column-name collisions are not de-duplicated (two canonical keys could in theory
  slugify to the same name) — acceptable with the current data; revisit if it occurs.
- Local file logging (`scraper.log`, `error_log.txt`) contradicts [[logging_conventions]]
  (stdout JSON). Retained for the local phase; must move to structured stdout for the Vercel
  Function in 0003.
- `price_gbp`/`year`/`miles` parsing is best-effort; malformed values become NULL or skip the row.

### AgentSoul tasks (edit files directly in its repo; never commit)

- Create `/Users/bennewton/Ai/AgentSoul/AgentSoul/Peewee_ORM_Choice.md` — OKF markdown,
  frontmatter `type: decision`, `tags`, `status: active`; `# Peewee ORM Choice` title;
  `## Related` and `## Update trigger` sections; links to `[[Approved_Dependencies]]` and
  `[[Environment_Contract]]`. Content: Peewee is a good, simple, modern Python ORM for small
  services; chosen for this scraper's single-table model and serverless (short-lived
  connection) fit.
- Add the new page to `AgentSoul_Index.md`.
- Add `peewee` to `Approved_Dependencies.md`. Do **not** add `psycopg2-binary` (user decision).

---

## Open Questions

1. **Postgres driver.** Neither `peewee` nor a Postgres driver is on
   `[[Approved_Dependencies]]`. **Resolved:** use `psycopg2-binary`; it is not added to the
   approved list.
2. **Connection-string shape & Peewee API** must be verified live via Context7 (unavailable
   while writing this spec) before coding — Supabase pooler host/port/user format and
   `playhouse.db_url.connect` / upsert specifics.
3. **Resolved:** price is stored as an integer in column `price_gbp` (currency symbol and
   commas stripped from the scraped string).
4. **Resolved:** the extras count is 53 (Porsche Taycan, 2026); update the stale "38" in AGENTS.md accordingly.

---

## Out of Scope

- The Vercel Function / scheduled cloud run — **spec 0003**.
- Migrating historical `.xlsx` data into the database.
- Supabase Row-Level Security, auth, roles, and the Supabase MCP (not assumed available).
- Analytics / reporting / dashboards over the data.
- Running `db-init` in Vercel or CI (deploy-time provisioning) — deferred to 0003.
- Destructive or renaming migrations and Alembic-style migration tooling; single-statement `ON CONFLICT` upserts.
- Replacing local-file logging with structured stdout JSON (deferred to 0003).

---

## Implementation Checklist

1. `add-deps` — Add `peewee`, Postgres driver, `python-dotenv`; add `pytest` (dev); remove
   `openpyxl`; remove `pandas` after swapping config read to stdlib `csv`.
2. `create-db-module` — `db.py`: `database` handle from `DATABASE_URL`, fail-fast config,
   `BaseModel`, dynamic `Listing` model, snake_case rule + `EXTRA_COLUMN_MAP`, `init_db()` +
   read-only `check_schema()`.
2a. `db-init` — `db_init.py` (single-transaction, advisory-locked, additive-only sync per
   Section 3a) and the `just db-init` recipe.
3. `upsert-logic` — `save_listings(rows)`: `ad_key` extraction + fallback, read-modify-write
   upsert, `first_found`/`last_found`/`days_live` handling.
4. `retry-loop` — retry constants, retryable exceptions, reconnect + exponential backoff,
   exhaustion → log + `error_log.txt` + skip + continue.
5. `wire-scraper` — `get_config`/`get_total_pages` call-site changes; inject
   make/model/model_type/postcode/ad_key; delete `get_file_name()` and `save_data()`.
6. `ty-ignores` — targeted `# type: ignore[unresolved-attribute]` for dynamic fields.
7. `tests` — `tests/` + SQLite-bound fixtures; implement acceptance scenarios 1–12;
   `just test` recipe.
8. `docs-and-env` — `.env.template`, AGENTS.md/README, Justfile `clean`, `TECH_DEBT.md`,
   AgentSoul page + index + approved-deps.
9. `validate` — `just check`, `just test`; `just db-init` run twice against Supabase (second run
   a no-op); then one real scrape confirming rows upsert and no `.xlsx` is produced; then
   update `STATE.md`.

---

## Simplifications made (simplify pass)

- **Single-statement `ON CONFLICT` upsert → read-modify-write in a transaction.** `days_live`
  needs the existing `first_found`, and read-modify-write behaves identically on SQLite and
  Postgres (so it is directly testable). `ON CONFLICT` moved to Out of Scope as a later
  optimisation.
- **Dropped `pandas` entirely** rather than keeping it just for one `read_csv` — replaced with
  stdlib `csv`. Removes a heavy transitive dependency from a serverless target.
- **No schema-migration framework.** One local, idempotent, additive-only `db_init.py`
  (`CREATE TABLE IF NOT EXISTS` + add missing columns) instead of Alembic; destructive changes
  stay manual and are recorded in `TECH_DEBT.md`. Running it in Vercel/CI is deferred to 0003.
- **No new config knobs** beyond `DATABASE_URL`. Retry tuning lives as named constants in
  `db.py`, not environment variables, until a second caller needs them.
- **Logging left as-is** for this phase (local files) with the stdout-JSON migration deferred
  to 0003 rather than reworked now — kept proportional to the local-only scope.
- **No connection pooling layer in-process** — one short-lived connection per run, relying on
  Supabase's pooler, matching what 0003 will need without extra code.
