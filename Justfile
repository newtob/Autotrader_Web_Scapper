default:
    @just --list

# --- deps ---

# Install / sync dependencies
[group('deps')]
sync:
    uv sync -q

# Show outdated dependencies
[group('deps')]
outdated:
    uv tree --outdated

# Update all dependencies to latest compatible versions
[group('deps')]
update:
    uv lock --upgrade && uv sync

# --- app ---

# Run the scraper, retrying every 5 min for up to 1 h (RETRY_WINDOW=0 = one attempt; needs Firefox + geckodriver on PATH)
[group('app')]
run:
    scripts/run_with_retry.sh

# Clean generated log files
[group('app')]
clean:
    rm -f error_log.txt
    rm -f scraper.log

# --- web ---

# Run the price gallery dev server (add ?fixture to the URL to use local sample data)
[group('web')]
web-dev:
    cd web && npm run dev

# Build the price gallery to web/dist
[group('web')]
web-build:
    cd web && npm run build

# Run the price gallery unit tests and type check
[group('web')]
web-test:
    cd web && npm test && npm run check

# --- rust api ---

# Format check, clippy and tests for the Rust prices API (set TEST_DATABASE_URL to a throwaway Postgres to include DB tests)
[group('rust')]
rust-check:
    cargo fmt --check
    cargo clippy --all-targets -- -D warnings
    cargo test -- --test-threads=1

# --- db ---

# Create / sync the database schema (idempotent; local only).
# Requires DATABASE_URL (see .env.template)
[group('db')]
db-init:
    uv run python db_init.py

# Backfill daily_stats from existing listings (idempotent)
# Requires DATABASE_URL and a prior `just db-init`
[group('db')]
db-backfill:
    uv run python backfill_stats.py

# --- quality ---

# Lint (report only)
[group('quality')]
lint:
    uv run ruff check -q .

# Check formatting without writing changes
[group('quality')]
fmt-check:
    uv run ruff format --check -q .

# Type-check with ty
[group('quality')]
typecheck:
    uv run ty check .

# Apply auto-fixable lint issues and format
[group('quality')]
fix:
    uv run ruff check --fix -q .
    uv run ruff format -q .

# Run the test suite (hermetic: SQLite, no DATABASE_URL needed)
[group('quality')]
test:
    uv run pytest -q

# Run all static-analysis checks (non-mutating)
[group('quality')]
check: lint fmt-check typecheck

# --- security ---

# Dependency security scan
[group('security')]
pysentry:
    uv run pysentry-rs .

# SAST scan (requires semgrep)
[group('security')]
semgrep:
    semgrep --config auto --quiet --error

# --- ci ---

# Everything CI runs; fails closed
[group('ci')]
ci: sync check test pysentry

# Validate the build: sync, CI checks, and SAST
[group('ci')]
build: ci semgrep
