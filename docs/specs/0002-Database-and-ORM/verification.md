# Verification: 0002 Database and ORM

This guide is a sanity layer. The proof is the automated test suite (`just test`). These steps
confirm, from a terminal, that the database layer works the way a person running the scraper
would experience it.

Run every command from the repository root, in one terminal session (the steps share shell
variables). Part 1 needs no credentials and no network, and is safe for an agent to run. Parts 2
and 3 are **human-run only**.

## Prerequisites

- `uv` and `just` are installed (`just sync` installs dependencies).
- For Part 1, no `.env` file is needed. Any `DATABASE_URL` you already have exported is replaced
  inside the steps; run `unset DATABASE_URL` first if in doubt.
- Migrations must actually run: the schema is created only by `just db-init`. The scraper never
  creates or alters tables, so "it started" does not prove the schema exists.

---

## Part 1: local SQLite (agent-runnable, no Supabase)

### 1.1 Create a scratch directory

```bash
unset DATABASE_URL
export VERIFY_DIR="$(mktemp -d)"
export DATABASE_URL="sqlite:///$VERIFY_DIR/verify.db"
echo "$DATABASE_URL"
```

Expected: one line, `sqlite:////.../verify.db` style, pointing into a temp directory. All DB files
below live there, never in the repo.

### 1.2 First `just db-init` creates the table

```bash
just db-init
echo "exit=$?"
```

Expected (timestamps vary):

```
uv run python db_init.py
HH:MM:SS [INFO] Created table 'listing' with 70 column(s)
exit=0
```

### 1.3 Second `just db-init` is a no-op

```bash
just db-init
echo "exit=$?"
```

Expected:

```
uv run python db_init.py
HH:MM:SS [INFO] Schema already up to date: no changes
exit=0
```

### 1.4 Inspect the schema

The expected column count is 17 fixed columns plus one per extra in `extras_prices.json`.

```bash
uv run python -c "import db; print(len(db.FIXED_COLUMNS) + len(db.EXTRA_COLUMN_MAP), len(db.EXTRA_COLUMN_MAP))"
uv run python -c "
import os, sqlite3
con = sqlite3.connect(os.environ['DATABASE_URL'].removeprefix('sqlite:///'))
cols = con.execute('PRAGMA table_info(listing)').fetchall()
print(len(cols))
print([c[1] for c in cols][:17])
print(con.execute('select count(*) from listing').fetchone()[0])
"
```

Expected:

```
70 53
70
['id', 'ad_key', 'title', 'price_gbp', 'year', 'miles', 'fuel_type', 'detail_url', 'make', 'model', 'model_type', 'postcode', 'extras_total', 'extras_other', 'first_found', 'last_found', 'days_live']
0
```

(If `extras_prices.json` has changed since this guide was written, the 70 and 53 change, but the
first number printed by each of the first two commands must still agree, and the table starts
empty.)

### 1.5 Upsert demo: insert, then re-see the same advert

This calls the real `db.save_listings` with a fixed clock: first sighting on 1 Jan 2026, second
sighting (at a lower price) three days later. It also submits a row with a blank title.

```bash
uv run python -c "
from datetime import UTC, datetime, timedelta
import db
ad = '202601011234567'
row = {'title': 'Porsche Taycan 4S', 'price': '£42,000', 'year': '2023 (73 reg)', 'miles': '12,345 miles',
       'fuel_type': 'Electric', 'detail_url': 'https://www.autotrader.co.uk/car-details/' + ad,
       'make': 'Porsche', 'model': 'Taycan', 'model_type': '4S', 'postcode': 'SW1A 1AA',
       'extras': {'150kW DC On-Board Booster': 294}, 'extras_other': 'Heated seats'}
db.init_db()
t0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
print(db.save_listings([row], clock=lambda: t0))
print(db.save_listings([{**row, 'price': '£41,500'}], clock=lambda: t0 + timedelta(days=3)))
r = db.Listing.get(db.Listing.ad_key == ad)
print(db.Listing.select().count(), r.price_gbp, r.extras_total, r.first_found.isoformat(), r.last_found.isoformat(), r.days_live)
print(db.save_listings([{**row, 'title': ' '}]))
db.close_db()
"
```

Expected (the WARNING line is a log message on stderr and may appear before the other output):

```
SaveResult(succeeded=True, attempts=1, saved=1, skipped=0)
SaveResult(succeeded=True, attempts=1, saved=1, skipped=0)
1 41500 294 2026-01-01T12:00:00+00:00 2026-01-04T12:00:00+00:00 3
SaveResult(succeeded=True, attempts=0, saved=0, skipped=1)
Skipping listing with no title (detail_url=https://www.autotrader.co.uk/car-details/202601011234567)
```

What this proves: one row after two sightings (upsert on the advert id, no duplicate), the price
was updated, `first_found` was kept, `last_found` advanced, `days_live` is 3, `extras_total` is
the sum of matched extras, and a bad row is skipped rather than crashing the page.

Confirm directly in SQLite:

```bash
uv run python -c "
import os, sqlite3
con = sqlite3.connect(os.environ['DATABASE_URL'].removeprefix('sqlite:///'))
print(con.execute('select ad_key, price_gbp, extras_total, extra_150kw_dc_on_board_booster, days_live from listing').fetchall())
"
```

Expected:

```
[('202601011234567', 41500, 294, 294, 3)]
```

### 1.6 `db-init` restores a missing extras column

Drop one extras column behind the tool's back, then re-run. The data row must survive.

```bash
uv run python -c "
import os, sqlite3
con = sqlite3.connect(os.environ['DATABASE_URL'].removeprefix('sqlite:///'))
con.execute('ALTER TABLE listing DROP COLUMN extra_150kw_dc_on_board_booster'); con.commit()
"
just db-init
just db-init
```

Expected (first run adds the column, second is a no-op):

```
uv run python db_init.py
HH:MM:SS [INFO] Added 1 column(s): added extra_150kw_dc_on_board_booster
uv run python db_init.py
HH:MM:SS [INFO] Schema already up to date: no changes
```

### 1.7 Missing or invalid `DATABASE_URL` fails clearly

```bash
DATABASE_URL= uv run python db_init.py; echo "exit=$?"
DATABASE_URL='mysql://u:SENTINEL@h/d' uv run python db_init.py; echo "exit=$?"
DATABASE_URL='mysql://u:SENTINEL@h/d' uv run python db_init.py 2>&1 | grep -c SENTINEL
```

Expected:

```
HH:MM:SS [ERROR] DATABASE_URL is not set. Copy .env.template to .env and set DATABASE_URL.
exit=1
HH:MM:SS [ERROR] DATABASE_URL is invalid: expected a scheme in postgres, postgresql, sqlite. See .env.template for the format.
exit=1
0
```

The last line proves the password-like value (`SENTINEL`) is never echoed. (`grep -c` exits 1
when it counts 0 matches; the `0` is the result that matters.)

### 1.8 No spreadsheet code remains

```bash
grep -nE "pandas|openpyxl|xlsx" auto_trader.py pyproject.toml; echo "grep exit=$?"
```

Expected: no matching lines, then `grep exit=1`.

### 1.9 Tests and full build

```bash
just test
just build
echo "build exit=$?"
```

Expected: `just test` ends with `102 passed` (the count grows if tests are added; zero failures is
what matters). `just build` runs sync, lint, format check, type check, the tests again, the
dependency scan (`0 vulnerable`) and semgrep, and the final line is `build exit=0`. `just build`
needs `semgrep` on PATH (`brew install semgrep`).

### 1.10 Clean up

```bash
rm -rf "$VERIFY_DIR"
unset VERIFY_DIR DATABASE_URL
git status --short | grep -E "verify\.db|error_log" || echo "clean"
```

Expected: `clean`.

### Part 1 expected results

| Step | Expected |
| ---- | -------- |
| 1.2 | Exit 0, "Created table 'listing' with 70 column(s)" |
| 1.3 | Exit 0, "Schema already up to date: no changes" |
| 1.4 | 70 columns (17 fixed + 53 extras), empty table |
| 1.5 | One row after two sightings; price updated; `first_found` kept; `last_found` advanced; `days_live` 3; blank-title row skipped |
| 1.6 | "Added 1 column(s)" then "no changes"; nothing dropped or retyped |
| 1.7 | Both failures exit 1, messages name `DATABASE_URL` and `.env.template`, `SENTINEL` count is 0 |
| 1.8 | No `pandas`, `openpyxl` or `xlsx` matches |
| 1.9 | All tests pass; `just build` exits 0 |
| 1.10 | No leftover DB or error-log files in the repo |

---

## Part 2: real scrape into local SQLite (HUMAN-RUN ONLY)

Needs Firefox, geckodriver on PATH, and live requests to autotrader.co.uk. Agents must not run
this. For a short run, set `autotrader_config.csv` and `make.json` to a single make before you
start (restore them afterwards), or stop the scraper with Ctrl+C after one page completes.

```bash
export VERIFY_DIR="$(mktemp -d)"
export DATABASE_URL="sqlite:///$VERIFY_DIR/verify.db"
touch "$VERIFY_DIR/marker"
just db-init
just run
```

After one page (or the whole run) completes, check:

```bash
find . -maxdepth 1 -name '*.xlsx' -newer "$VERIFY_DIR/marker"
uv run python -c "
import os, sqlite3
con = sqlite3.connect(os.environ['DATABASE_URL'].removeprefix('sqlite:///'))
print(con.execute('select count(*), count(distinct ad_key) from listing').fetchone())
for r in con.execute('select ad_key, price_gbp, first_found, last_found, days_live from listing limit 5'): print(r)
"
```

Expected: `find` prints nothing (older `.xlsx` files from before this phase may exist; only new ones
matter). The two counts are equal and greater than 0. Each `ad_key` is 15 digits. `first_found`
equals `last_found` and `days_live` is 0 on a first run.

Run `just run` a second time against the same `DATABASE_URL`, stop after the same page, and
repeat the query. Expected: the row count has not grown for re-seen adverts, `first_found` is
unchanged, and `last_found` is later.

```bash
rm -rf "$VERIFY_DIR"
unset VERIFY_DIR DATABASE_URL
```

| Step | Expected |
| ---- | -------- |
| Scrape | Runs without a schema error; no new `.xlsx` |
| Counts | `count(*)` equals `count(distinct ad_key)`, greater than 0 |
| Re-run | No duplicates; `first_found` unchanged; `last_found` advanced |

---

## Part 3: real Supabase (HUMAN-RUN ONLY)

Needs real credentials. Agents must not read `.env` or the Supabase password. Never paste the
URL into chat, logs or a commit.

1. Create `.env` and fill in the transaction-pooler URL (port 6543). `sslmode=require` is added
   automatically if the URL does not set `sslmode` itself; setting it explicitly is fine too:

```bash
cp .env.template .env
```

2. Provision the schema twice. The second run must report no changes.

```bash
just db-init
just db-init
```

Expected: first run "Created table 'listing' with 70 column(s)" (or "no changes" if it exists),
second run "Schema already up to date: no changes". Both exit 0.

3. In the Supabase dashboard SQL editor for project `AutoTrader_Scraper`:

```sql
select column_name, data_type from information_schema.columns where table_name = 'listing' order by ordinal_position;
```

Expected: `first_found` and `last_found` are `timestamp with time zone`; the extras columns are
`integer`.

4. Run the scraper, then check for duplicates in the SQL editor:

```bash
just run
```

```sql
select count(*), count(distinct ad_key) from listing;
```

Expected: the two counts are equal.

5. Run `just run` again. Expected: `first_found` is unchanged for re-seen adverts and `last_found`
   is later.

6. Confirm no spreadsheet and no leaked credentials:

```bash
find . -maxdepth 1 -name '*.xlsx' -mmin -60
grep -c "postgresql://" scraper.log
```

Expected: no new `.xlsx`; the `grep -c` prints `0`.

| Step | Expected |
| ---- | -------- |
| db-init twice | Created/no-op, then "no changes"; both exit 0 |
| Column types | Timestamps `timestamp with time zone`, extras `integer` |
| Scrape | `count(*)` equals `count(distinct ad_key)` |
| Second scrape | `first_found` unchanged, `last_found` advanced |
| Hygiene | No new `.xlsx`; `scraper.log` has no `postgresql://` |
