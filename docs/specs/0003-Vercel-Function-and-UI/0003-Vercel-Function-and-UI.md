---
uat: guide
---

# Spec 0003 — Vercel Function and Price Dashboard

## Overview

Phases 0001–0002 produce a scraper that upserts listings into a Supabase Postgres `listing` table.
This phase puts the whole thing in the cloud and adds a front end:

1. **Daily run on Vercel.** A Vercel Cron Job calls a Python Vercel Function that runs the scraper,
   upserts listings (unchanged from 0002), then writes one summary row per search per day to a new
   `daily_stats` table.
2. **Read API.** A second, read-only Python Vercel Function, `GET /api/stats`, returns the daily
   series as JSON.
3. **Front end.** A static **Svelte + Vite** single page (`web/`), built and hosted by the same
   Vercel project, calls `/api/stats` and shows three line charts (cars on the market, average
   price in GBP, average days live) with one shared Month / Year / All time selector, a
   percent-change badge on every chart, a Light / Dark theme selector, and a "Currently searching
   for" box built from `make.json`.

The visual design is prototyped in `design/dashboard/Main.dc.html` (a Design canvas file). It is a
**visual reference to port, not code to ship**; the Svelte app reproduces its layout, type, colours
and behaviour.

Usage is very low (under one visitor a day), so the design favours fewest moving parts over
scale: no auth, no server-side sessions, no in-process caching layer, CDN caching only.

### Interpretation of "this will run the python scraper and this visual builder"

Taken to mean: **one Vercel project** both (a) runs the Python scraper on a schedule and (b) builds
and serves the Svelte UI. If "visual builder" was meant differently (for example, the function
*generating* the page at run time), correct this before implementation.

### Feasibility gate — read before building (see Risks)

The scraper drives **Firefox through Selenium**. A Vercel Function has no browser, a bundle size
limit and a maximum duration. Running the scraper *inside* the function is therefore the riskiest
part of this phase and is **gated by a spike (Section 9, step 1)** before any other work depends on
it. Everything else here (`daily_stats`, `/api/stats`, the UI) is independent of *where* the
scraper runs. If the spike fails, the documented fallback (Risks, R1) moves only the scraper host
and leaves the rest of this spec unchanged.

---

## Technical Requirements

### 1. Repository layout (new and changed)

```
api/
  scrape.py          # Vercel Function — cron target; runs scrape + stats
  stats.py           # Vercel Function — read-only JSON for the UI
web/                 # Svelte + Vite app (TypeScript)
  index.html
  package.json
  vite.config.ts
  src/
    main.ts
    App.svelte
    lib/
      api.ts         # fetch + type of /api/stats
      format.ts      # £ formatting, search-details text, dates
      range.ts       # Month/Year/All slicing + percent change
      theme.ts       # light/dark state, persistence
      LineChart.svelte
      ChangeBadge.svelte
      RangeSelector.svelte
      ThemeToggle.svelte
      SearchDetails.svelte
    app.css          # design tokens as CSS custom properties (light + dark)
stats.py             # compute_daily_stats() — shared by api/scrape.py and tests
db.py                # + DailyStat model, check_schema covers it, save_daily_stats()
db_init.py           # + creates/syncs daily_stats
vercel.json          # build, output dir, cron, function limits
design/dashboard/    # design reference (already in repo)
```

`stats.py` at the repo root holds the pure logic (aggregation, series shaping) so it is testable
without Vercel. `api/*.py` are thin HTTP wrappers only.

### 2. Database — new table `daily_stats`

One row per **calendar day (UTC)** per **search**. A search is identified by
`(make, model, model_type)`; `model_type` is `''` (not NULL) when `make.json` has no
`aggregatedTrim`, so the unique key behaves the same on Postgres and SQLite.

| Column          | Type (PG / Peewee)                 | Null?    | Notes                                                                 |
| --------------- | ---------------------------------- | -------- | --------------------------------------------------------------------- |
| `id`            | serial / `AutoField`               | NOT NULL | Surrogate key.                                                        |
| `stat_date`     | date / `DateField`                 | NOT NULL | UTC calendar day the snapshot describes.                              |
| `make`          | text / `CharField`                 | NOT NULL | From `listing.make`.                                                  |
| `model`         | text / `CharField`                 | NOT NULL | `''` when blank.                                                      |
| `model_type`    | text / `CharField`                 | NOT NULL | `''` when no trim.                                                    |
| `car_count`     | integer / `IntegerField`           | NOT NULL | Cars on sale that day (Section 3).                                    |
| `avg_price_gbp` | integer / `IntegerField(null)`     | NULL     | Mean `price_gbp`, rounded to whole pounds; NULL when `car_count = 0`. |
| `avg_days_live` | numeric(6,1) / `DecimalField(null)`| NULL     | Mean `days_live`, one decimal place; NULL when `car_count = 0`.       |
| `computed_at`   | timestamptz / `DateTimeField`      | NOT NULL | When this row was last written.                                       |

Unique index on `(stat_date, make, model, model_type)`.

- Provisioned by `just db-init` exactly as `listing` is (additive-only, idempotent, advisory-locked
  single transaction, Spec 0002 §3a). `check_schema()` is extended to require `daily_stats`; the
  scraper still never runs DDL.
- **Why a stats table at all:** `listing.price_gbp` is overwritten on every re-sight, so historical
  average price cannot be recovered later. A daily snapshot is the only way to build the price
  history. Count and days-live could be derived from `first_found`/`last_found`, but one writer for
  all three keeps the series consistent and makes `/api/stats` a trivial read.
- **No backfill.** The price series starts on the first run after this phase ships (see Out of
  Scope).
- Row-Level Security: `daily_stats` and `listing` are accessed only through the pooler
  `DATABASE_URL` held server-side in Vercel; the browser never talks to Supabase. Enable RLS with
  no policies on both tables so the public anon key can read nothing (documented, applied by hand
  in the Supabase dashboard; not automated here).

### 3. Computing daily stats (`stats.py`)

`compute_daily_stats(run_date: date) -> list[DailyStatRow]` runs **after the scrape finishes**:

- A car is "on sale on `run_date`" if its `listing.last_found` falls on `run_date` (UTC), i.e. it
  was seen by today's run. (The scrape upserts `last_found = now` on every sighting, Spec 0002 §6.)
- Group by `(make, model, model_type)` and compute `COUNT(*)`, `ROUND(AVG(price_gbp))`,
  `ROUND(AVG(days_live), 1)`.
- **Searches with zero cars today** still get a row (`car_count = 0`, averages NULL) for every
  search in `make.json`, so a gap in the chart means "the scraper did not run", never "no cars".
  The set of searches comes from `make.json` (the source of truth for what is tracked).
- `save_daily_stats(rows)` upserts on the unique key (read-modify-write in a transaction, same
  pattern and retry policy as `save_listings`, Spec 0002 §6–7). **Re-running on the same day
  overwrites that day's row**; it never creates duplicates.
- If the scrape fails part-way (some searches did not complete), stats are **still computed for
  the searches that completed** and the run reports partial failure (Section 4). A search whose
  scrape threw before saving any page is skipped (no row) rather than written as `0`, so a failed
  scrape cannot masquerade as an empty market.

### 4. Vercel Function — `api/scrape.py` (cron target)

- **Trigger:** Vercel Cron Job, `GET /api/scrape`, once a day. Schedule `0 6 * * *` (06:00 UTC);
  Vercel invokes within the scheduled hour. Daily is the maximum frequency on the Hobby plan.
- **Auth:** Vercel sends `Authorization: Bearer <CRON_SECRET>`. The function compares in constant
  time and returns `401` without running anything on a mismatch or when `CRON_SECRET` is unset.
  Never log the header or the secret. `/api/scrape` is not linked from the UI and does not set CORS
  headers.
- **Flow:**
  1. Verify auth.
  2. `load_dotenv()` is **not** called in the function (cloud env only), consistent with Spec 0002.
  3. `db.init_db()` (fail fast on missing `DATABASE_URL` / stale schema → `just db-init`).
  4. `run_scrape()` — the existing scraper logic refactored out of `main()` into a callable
     (Section 5) that takes the browser as a parameter.
  5. `compute_daily_stats(today)` → `save_daily_stats(rows)`.
  6. `db.close_db()` in a `finally`.
  7. Respond `200` with `{"ok": true, "listings_saved": n, "searches_completed": k, "searches_total": m}`
     or `207`/`500` with `{"ok": false, "error": "<short message>"}` on partial/total failure. No
     stack traces or connection strings in the body.
- **Overlap protection:** session-level advisory locks don't work on the transaction pooler, so
  use a `scrape_runs` guard: insert a `scrape_runs` row (`started_at`, `finished_at`, `status`)
  and refuse to start (`409`) if a run started in the last 2 hours and has no `finished_at`. A manual re-trigger during a run therefore
  cannot double-scrape. (Small table, provisioned by `just db-init`; columns `id`, `started_at`
  timestamptz, `finished_at` timestamptz null, `status` text, `detail` text null.)
- **Logging:** structured JSON lines to **stdout** (Vercel captures it), replacing the local
  `scraper.log` / `error_log.txt` files in the cloud path. This closes the Spec 0002 TECH_DEBT item
  on logging. Local runs may still write the files.
- **Limits:** `vercel.json` sets `functions["api/scrape.py"].maxDuration` to the plan maximum. See
  Risks R2 for what happens when a full scrape does not fit.

### 5. Making the scraper host-agnostic (`auto_trader.py`)

To let the same code run locally and in the cloud, with minimal change:

- Extract the body of `main()` after `init_db()` into `run_scrape(driver) -> ScrapeResult`
  (`listings_saved`, `searches_completed`, `searches_total`, `errors`). `main()` becomes: configure
  logging, `load_dotenv()`, `init_db()`, build local Firefox driver, `run_scrape(driver)`,
  `compute_daily_stats` + save, `close_db()`. **Local behaviour is unchanged** apart from the new
  stats step.
- Add `build_driver()`: if `SELENIUM_REMOTE_URL` is set, return a `webdriver.Remote(...)` with the
  same Firefox options (eager page-load strategy, cookie setup unchanged); otherwise a local
  `webdriver.Firefox`. Keeps the existing module-level `driver`/`wait` convention.
- New optional env vars (all server-side only):

| Variable              | Required | Notes                                                                        |
| --------------------- | -------- | ---------------------------------------------------------------------------- |
| `DATABASE_URL`        | yes      | Unchanged (Spec 0002).                                                       |
| `CRON_SECRET`         | yes (cloud) | Shared secret Vercel sends to `/api/scrape`. Random, ≥ 32 chars.          |
| `SELENIUM_REMOTE_URL` | cloud, if R1 option A | URL of a hosted WebDriver/browser service. Contains credentials: treat as a secret, never log. |

`.env.template` documents each, with format and required status.

### 6. Vercel Function — `api/stats.py` (read API)

`GET /api/stats` — public, read-only, no auth, no parameters.

**Response `200`, `Content-Type: application/json`:**

```json
{
  "generated_at": "2026-10-23T06:14:02Z",
  "searches": [
    {
      "make": "Porsche",
      "model": "Taycan",
      "model_type": "4S",
      "fuel_type": "Electric",
      "price_to": 42000,
      "series": [
        { "date": "2026-10-22", "count": 52, "avg_price_gbp": 43500, "avg_days_live": 31.2 },
        { "date": "2026-10-23", "count": 51, "avg_price_gbp": 43410, "avg_days_live": 31.8 }
      ]
    }
  ]
}
```

- `searches` is built from `make.json` (so the "Currently searching for" box always matches what
  is actually tracked); each `series` is that search's `daily_stats` rows ordered by `date`
  ascending. `model_type`, `fuel_type` and `price_to` are omitted when absent in `make.json`.
- Searches in `make.json` with no `daily_stats` rows yet return `"series": []`.
- **Caching:** `Cache-Control: public, s-maxage=3600, stale-while-revalidate=86400`. Data changes
  once a day; at under one visitor a day almost every request is a CDN hit and the database is
  touched rarely. No other cache layer.
- **Errors:** database failure → `503` `{"error": "data unavailable"}` with `Cache-Control: no-store`;
  the body never contains driver messages or connection details.
- `make.json` is bundled with the function (`includeFiles` in `vercel.json`).
- The endpoint exposes only aggregated, non-sensitive numbers and search criteria already shown
  on the page. It never returns individual listings, URLs or `ad_key`s.

### 7. Front end — `web/` (Svelte + Vite)

**Stack:** Svelte 5, Vite, TypeScript, plain CSS custom properties. **No chart library**: each
chart is a small inline-SVG component (`LineChart.svelte`) matching the prototype, which keeps the
dependency list to Svelte/Vite/TypeScript/Vitest. (Revisit if interactive tooltips become a
requirement; see Out of Scope.)

**Page structure (top to bottom), per `design/dashboard/Main.dc.html`:**

1. Theme selector (Light / Dark) top right.
2. `h1` **"Autotrader used car prices"**.
3. Subtext: **"Autotrader is scraped every day for used cars that fit the description"**.
4. **Search details** box, deliberately less prominent (muted panel, small type). Heading
   "Currently searching for"; one line per search, formatted from the API's `searches[]`:
   `{make} {model}, {model_type}, up to: £{price_to}` e.g. `Porsche Taycan, 4S, up to: £42,000`.
   Missing parts are dropped with their separator (no trim → `Porsche Taycan, up to: £42,000`; no
   price ceiling → `Porsche Taycan, 4S`). Prices use `en-GB` grouping. `fuel_type` is returned by
   the API but **not displayed** (matches the requested format).
5. **Market trends** heading with the shared **Month / Year / All time** selector.
6. For each search, three chart cards in this order: **Cars on the market**, **Average price**
   (GBP), **Average days live**. With several searches, each search gets its own labelled group
   of three cards; the range selector and theme apply to all.

**Range selection (shared state, one source for all charts):**

| Option   | Window                                                       |
| -------- | ------------------------------------------------------------ |
| Month    | Last 30 days ending at the latest data point                 |
| Year     | Last 365 days ending at the latest data point                |
| All time | Every data point available                                   |

Windows are anchored on the **latest `date` in the data**, not the browser clock, so a stale
dataset still shows its most recent period. If fewer days exist than the window, use all that exist.
The selector is a real `<button aria-pressed>` group (≥ 44 px targets); default is **Month**.

**Percent change (every chart).** For the selected window, `change = (last − first) / first × 100`,
using the first and last data points that have a non-null value. Shown in a badge on each card:

- Format: signed whole percent with a proper minus sign, plus a label naming the metric and the
  period, e.g. `−10% cars on the market this month`, `+3% average price this year`,
  `+8% average days live all time`. Period words: *this month*, *this year*, *all time*.
- Rounded to the nearest whole percent; `0%` shown as `0%` with no sign.
- Fewer than two usable points, or `first = 0` → badge shows `–` and the label "not enough data".
- Direction is conveyed by sign and by colour, and the two colours differ in **lightness as well
  as hue** (orange for a fall, blue for a rise), so colour is never the only signal. The badge
  makes no good/bad judgement: a falling price is not styled as "bad".

**Chart rendering.** Line + soft area fill, 4 y-axis ticks with metric-appropriate formatting
(`£43,500`, `52`, `31`), first and last dates under the x-axis, y-range padded 15% so a flat series
is not stretched. A single data point renders as a dot. An empty series renders an empty-state
message ("No data yet — the first daily scrape will appear here") instead of an empty chart. Each
SVG has `role="img"` and an `aria-label` describing the metric and window, plus a visually-hidden
table or summary of first, last and change for screen readers.

**Theme.**

- Light and dark token sets defined once as CSS custom properties (values from the prototype:
  dark `--bg #0B1220`, `--card #131C2E`, `--ink #E8EEF9`, `--muted #9AA9C2`, `--accent #5EA2F0`;
  light `--bg #F4F6F9`, `--card #FFFFFF`, `--ink #0B1B33`, `--muted #44546A`, `--accent #0A5CAD`;
  full list in `app.css`). All text meets 4.5:1 contrast in both themes.
- Initial theme: saved choice (`localStorage`, wrapped in try/catch) → OS `prefers-color-scheme`
  → **dark**. Selecting a theme saves it. The theme is set before first paint (inline script in
  `index.html` sets `data-theme`) so there is no flash of the wrong theme.

**Loading / error states.** While loading: skeleton cards. On fetch failure or non-200: a single
inline message "Couldn't load the latest prices. Try again later." with a Retry button; no stack
traces. Stale-while-revalidate means a cached response may be up to a day old; the page shows
"Data as of {generated_at date}" under the subtext.

**Responsive.** One column, max width 1120 px, works at 360 px wide (selectors wrap, charts scale
by `viewBox`), no horizontal page scroll.

**Dev workflow.** `just web-dev` runs Vite with `/api` proxied to a local stub or to `vercel dev`;
`just web-build` builds to `web/dist`. A tiny fixture (`web/src/lib/fixture.json`) lets the UI run
without a database.

### 8. `vercel.json`

```json
{
  "buildCommand": "cd web && npm ci && npm run build",
  "outputDirectory": "web/dist",
  "crons": [{ "path": "/api/scrape", "schedule": "0 6 * * *" }],
  "functions": {
    "api/scrape.py": { "maxDuration": 300, "includeFiles": "{auto_trader.py,db.py,stats.py,extras_prices.json,make.json,autotrader_config.csv}" },
    "api/stats.py": { "maxDuration": 10, "includeFiles": "{db.py,stats.py,extras_prices.json,make.json}" }
  }
}
```

> The exact `vercel.json` keys, the Python runtime / `pyproject.toml`+`uv.lock` dependency
> handling, `maxDuration` ceilings per plan, `includeFiles` globbing and cron auth header must be
> **re-verified against current Vercel docs (Context7 or vercel.com/docs) at implementation time**.
> The values above are placeholders from memory, not confirmed.

Environment variables are set in the Vercel dashboard per environment. **Preview deployments must
not carry the production `DATABASE_URL`** (use a separate Supabase project or leave unset so
previews fail safe), and Preview cron jobs do not run. `.env` is never deployed.

### 9. Risks

**R1 — Selenium/Firefox cannot run in a Vercel Function.** Vercel's Python runtime provides no
Firefox or geckodriver, and function bundles are size-limited. Options, in order of preference:

- **A. Remote browser (spec default).** `build_driver()` returns `webdriver.Remote` pointed at a
  hosted WebDriver/browser service via `SELENIUM_REMOTE_URL`; the function only orchestrates and
  parses. Cost and AutoTrader's bot-detection behaviour from a datacentre IP are unknown and are
  checked in the spike.
- **B. Different host for the scraper only.** A scheduled GitHub Actions workflow (the repo already
  has `.github/`) runs `just run` on a runner that has Firefox and writes to Supabase, then calls
  `/api/stats` revalidation or simply relies on the CDN TTL. `api/scrape.py` shrinks to a
  stats-only endpoint or is removed. Needs no remote-browser spend. Everything else is unchanged.
- **C. Rewrite the scraper to plain HTTP.** Not recommended: AutoTrader renders via client-side
  JS and a consent wall (Spec 0001); fragile and more likely to be blocked.

**R2 — Duration.** The scraper sleeps 1–3 s repeatedly per page and visits every advert's detail
page (up to 101 pages per search). A full run can exceed a Vercel function's maximum duration. The
spike measures a real run. If it does not fit: split work per search/page range with a `resume`
cursor stored in `scrape_runs` and self-invoked in chunks, **or** take option R1-B. Do not silently
truncate results.

**R3 — Terms of service.** Unchanged from the README disclaimer: personal research, low load.
Moving to a cloud IP and a daily schedule does not change that; do not increase frequency.

**R4 — Supabase free-tier pausing.** Inactive free projects pause. The daily write keeps the
project active; a failed scraper for over a week could pause it and take the dashboard down. Noted
in TECH_DEBT; mitigated by `/api/stats` returning `503` with a clear UI message.

### 10. Testing

Hermetic where possible: SQLite-bound `database`, no network, no Firefox, no Vercel runtime.

**Python (`just test`):**

1. **Stats on first run** — *Given* three listings last seen today across two searches, *When*
   `compute_daily_stats(today)` runs, *Then* one row per search with the correct `car_count`,
   rounded `avg_price_gbp` and one-decimal `avg_days_live`.
2. **Only cars seen today count** — *Given* a listing with `last_found` yesterday, *Then* it is
   excluded from today's row.
3. **Zero-car search still gets a row** — *Given* a `make.json` search with no listings today,
   *Then* a row exists with `car_count = 0` and NULL averages.
4. **Failed search writes no row** — *Given* a search whose scrape raised before any page was
   saved, *Then* no `daily_stats` row is written for it.
5. **Idempotent re-run** — *When* stats are computed and saved twice on the same day, *Then* one
   row per search exists and `computed_at` is updated.
6. **`model_type` empty-string identity** — *Given* a search with no trim, *Then* the unique key
   works and re-saving upserts rather than duplicating.
7. **`db-init` provisions `daily_stats` and `scrape_runs`**, is idempotent, and `check_schema()`
   fails naming `just db-init` when either is missing (extends 0002 scenarios 8–12).
8. **`/api/stats` shape** — *Given* seeded `daily_stats`, *When* the handler is invoked directly,
   *Then* the JSON matches the contract, series are date-ascending, absent optional fields are
   omitted, an unseeded search returns `"series": []`, and cache headers are present.
9. **`/api/stats` DB failure** — *Given* a DB that raises, *Then* `503` with `no-store` and a body
   containing no driver text.
10. **`/api/scrape` auth** — *Given* no/incorrect/unset `CRON_SECRET`, *Then* `401` and no scrape or
    DB call occurs; with the right secret the handler runs (scraper faked).
11. **`/api/scrape` overlap guard** — *Given* an unfinished `scrape_runs` row started 10 minutes
    ago, *Then* `409` and no scrape runs; *Given* one older than 2 hours, *Then* it proceeds.
12. **`build_driver` selection** — *Given* `SELENIUM_REMOTE_URL` set/unset, *Then* a Remote /
    local Firefox driver is requested (webdriver faked); the URL is never logged.
13. **No secrets in output** — handler responses and logs never contain `DATABASE_URL`,
    `CRON_SECRET` or `SELENIUM_REMOTE_URL` values.

**Front end (Vitest + `@testing-library/svelte`, `just web-test`):**

14. **Search-details formatting** — full entry → `Porsche Taycan, 4S, up to: £42,000`; no trim; no
    price ceiling; neither.
15. **Range slicing** — Month/Year/All slice the series anchored on the latest date; fewer days
    than the window uses all available.
16. **Percent change** — falling, rising, flat (`0%`), first-is-zero and single-point cases give the
    expected text; minus sign is `−` (U+2212).
17. **One selector, three charts** — changing the range updates all three charts' windows and all
    three badges' period labels together.
18. **Badge on every chart** — each of the three cards renders a badge naming its own metric.
19. **Theme** — toggle flips `data-theme`, persists to `localStorage`, honours a saved value over
    OS preference, and falls back to dark when storage throws.
20. **States** — loading skeleton, fetch-failure message with Retry, empty series message.
21. **Accessibility** — every chart has an accessible name; selector buttons expose `aria-pressed`;
    no horizontal overflow at 360 px (checked in a browser at verification time).

**Manual verification (`verification.md`, written at plan time):** deploy to a Vercel Preview with
a non-production database; trigger `/api/scrape` with the secret; confirm a `daily_stats` row
appears; load the page and confirm all three charts, badges, selectors and both themes.

---

## Data Flow

```
Vercel Cron (06:00 UTC, Authorization: Bearer CRON_SECRET)
  └─ GET /api/scrape  (api/scrape.py)
       ├─ verify CRON_SECRET → 401
       ├─ db.init_db()                       # check_schema incl. daily_stats, scrape_runs
       ├─ scrape_runs guard                  # 409 if a run is in flight
       ├─ run_scrape(build_driver())         # Selenium (remote or local) → db.save_listings()
       ├─ compute_daily_stats(today)         # listing → per-search aggregates
       ├─ save_daily_stats(rows)             # upsert on (stat_date, make, model, model_type)
       └─ 200 / 207 / 500 summary JSON

Browser
  └─ GET /  → static Svelte app (web/dist, Vercel CDN)
       └─ fetch /api/stats  (api/stats.py, CDN-cached 1 h)
            └─ daily_stats rows + make.json searches → JSON
       └─ client: slice by range → chart + percent-change badge ×3, theme, search details
```

---

## Documentation / state updates

- **README.md / AGENTS.md (`.github/copilot-instructions.md`):** document `api/`, `web/`,
  `daily_stats`, the new env vars, new `just` recipes, and the deploy steps.
- **Justfile:** add `web-dev`, `web-build`, `web-test`; extend `test` to remain Python-only and
  `check` to include the web typecheck/lint.
- **`.env.template`:** add `CRON_SECRET`, `SELENIUM_REMOTE_URL` (commented, with format).
- **`TECH_DEBT.md`:** close the "local file logging" item for the cloud path; add R4 and the
  deferrals under Out of Scope.
- **`.gitignore`:** `web/node_modules`, `web/dist`, `.vercel`.
- **`STATE.md`:** set 0003 to `done` **only once implemented and verified**, not at spec time.
- **AgentSoul:** add a decision page for the Vercel + Svelte/Vite architecture and the
  feasibility outcome of the spike (OKF markdown, `type: decision`); update `AgentSoul_Index.md`.
  Add Svelte/Vite/Vitest to `Approved_Dependencies.md` if that policy applies to JS tooling.

---

## Open Questions

1. **Spike outcome (R1/R2).** Which of R1 options A/B applies, and what is the measured full-run
   duration? Decided by Implementation Checklist step 1, before anything else is built.
2. **"Visual builder"** — confirm the interpretation in the Overview.
3. **Multiple postcodes.** `autotrader_config.csv` can hold several rows. Stats aggregate across
   postcodes per search; confirm that is wanted (a per-postcode split is not designed here).
4. **Custom domain / access control.** The dashboard and `/api/stats` are public. Confirm that is
   acceptable, or add Vercel password protection (plan-dependent) as a follow-up.
5. **Remote browser vendor** (if R1-A): choose one and confirm cost; none is assumed.

---

## Out of Scope

- Backfilling `daily_stats` from historical data (price history cannot be recovered; count and
  days-live could be derived from `first_found`/`last_found` as a later optional script).
- Chart tooltips, zoom, per-listing tables, comparing searches on one chart, CSV export.
- Alerts/notifications (e.g. price-drop emails).
- User accounts, auth on the dashboard, rate limiting beyond the CDN cache.
- Supabase Realtime, direct browser access to Supabase, the Supabase anon key.
- Destructive migrations, Alembic, single-statement `ON CONFLICT` (unchanged from 0002).
- Increasing scrape frequency beyond once a day.
- Server-side rendering / SvelteKit.

---

## Implementation Checklist

1. `spike` — **Feasibility gate.** Prove one Selenium page load + one detail-page extraction from a
   Vercel Function using a remote browser (R1-A); measure a full-run duration and bundle size.
   Record the outcome (A, or fall back to B) in this spec and AgentSoul before continuing.
2. `schema` — `DailyStat` and `ScrapeRun` models in `db.py`; extend `db_init.py` and
   `check_schema()`; tests 7.
3. `stats-logic` — `stats.py`: `compute_daily_stats`, `save_daily_stats` (with retry); tests 1–6.
4. `scraper-refactor` — `run_scrape(driver)`, `build_driver()`, stats step in `main()`;
   stdout JSON logging for the cloud path; test 12–13.
5. `api-stats` — `api/stats.py`; tests 8–9.
6. `api-scrape` — `api/scrape.py` with auth, overlap guard, summary response; tests 10–11.
7. `web-scaffold` — `web/` Vite + Svelte + TypeScript + Vitest, tokens/theme, fixture data.
8. `web-components` — `range.ts`, `format.ts`, `LineChart`, `ChangeBadge`, `RangeSelector`,
   `ThemeToggle`, `SearchDetails`, `App`; tests 14–21.
9. `vercel-config` — `vercel.json`, `.gitignore`, env-var documentation; verify config keys against
   live Vercel docs.
10. `docs-and-env` — README, AGENTS.md, Justfile recipes, `.env.template`, TECH_DEBT, AgentSoul.
11. `validate` — `just check`, `just test`, `just web-test`; Preview deployment against a
    non-production database; trigger `/api/scrape`; verify the dashboard end to end in both
    themes; then update `STATE.md`.
