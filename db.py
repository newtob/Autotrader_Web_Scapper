"""Database access for the AutoTrader scraper (Peewee + Supabase Postgres).

This module owns the Peewee database handle, the ``Listing`` model (fixed columns plus one
integer column per extra in ``extras_prices.json``), the read-only schema check, and the
``save_listings`` upsert with its retry loop. Schema provisioning lives in ``db_init.py``.

Nothing here connects at import time and nothing here loads ``.env``; entrypoints do that.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import peewee
from playhouse import db_url

logger = logging.getLogger("autotrader.db")

MAX_DB_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (peewee.OperationalError, peewee.InterfaceError)

DATABASE_URL_ENV = "DATABASE_URL"
ALLOWED_URL_SCHEMES: tuple[str, ...] = ("postgres", "postgresql", "sqlite")
ERROR_LOG_PATH = Path("error_log.txt")
EXTRAS_PRICES_PATH = Path(__file__).resolve().parent / "extras_prices.json"
TABLE_NAME = "listing"
FALLBACK_KEY_PREFIX = "nokey_"

FIXED_COLUMNS: tuple[str, ...] = (
    "id",
    "ad_key",
    "title",
    "price_gbp",
    "year",
    "miles",
    "fuel_type",
    "detail_url",
    "make",
    "model",
    "model_type",
    "postcode",
    "extras_total",
    "extras_other",
    "first_found",
    "last_found",
    "days_live",
)

_AD_KEY_RE = re.compile(r"\d{15}")


class ConfigError(RuntimeError):
    """Raised when ``DATABASE_URL`` is missing or invalid. Never contains the value."""


class DatabaseUnavailableError(RuntimeError):
    """The database could not be reached (network, auth, pooler). The message is safe to log."""


class SchemaError(RuntimeError):
    """Raised when the live ``listing`` table is missing or lacks expected columns."""


@dataclass(frozen=True)
class SaveResult:
    """Outcome of one ``save_listings`` call.

    Attributes:
        succeeded: ``False`` only when every attempt hit a retryable error.
        attempts: Number of attempts made (0 when nothing needed saving).
        saved: Rows inserted or updated.
        skipped: Rows dropped for a missing title or unparseable price.
    """

    succeeded: bool
    attempts: int
    saved: int
    skipped: int


class ListingRow(TypedDict):
    """One scraped listing as built by ``auto_trader.get_total_pages``."""

    title: str | None
    price: str | None
    year: str | None
    miles: str | None
    fuel_type: str | None
    detail_url: str | None
    make: str
    model: str | None
    model_type: str | None
    postcode: str
    extras: dict[str, int]
    extras_other: str


# ---------------------------------------------------------------------------
# Extras column derivation
# ---------------------------------------------------------------------------


MAX_IDENTIFIER_LENGTH = 63  # Postgres silently truncates longer identifiers, which breaks the column diff.


def snake_case(name: str) -> str:
    """Convert a canonical extra name to a Postgres-friendly column name.

    Lowercases, collapses runs of non-alphanumerics to one underscore, strips leading and
    trailing underscores, prefixes ``extra_`` when the result starts with a digit, and truncates
    to ``MAX_IDENTIFIER_LENGTH`` (Postgres' identifier limit).

    Args:
        name: Canonical extra name, e.g. ``"150kW DC On-Board Booster"``.

    Returns:
        The column name, e.g. ``"extra_150kw_dc_on_board_booster"``.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if slug[:1].isdigit():
        slug = f"extra_{slug}"
    return slug[:MAX_IDENTIFIER_LENGTH].rstrip("_")


def build_extra_column_map(names: Iterable[str]) -> dict[str, str]:
    """Map canonical extra names to column names, preserving order.

    Args:
        names: Canonical extra names.

    Returns:
        Ordered mapping of canonical name to column name.

    Raises:
        ValueError: If two names slugify to the same column, or a slug collides with a fixed column.
    """
    mapping: dict[str, str] = {}
    owners: dict[str, str] = {}
    for name in names:
        column = snake_case(name)
        if column in FIXED_COLUMNS:
            raise ValueError(f"Extra {name!r} maps to column {column!r}, which collides with a fixed column")
        if column in owners and owners[column] != name:
            raise ValueError(f"Extras {owners[column]!r} and {name!r} both map to column {column!r}")
        owners[column] = name
        mapping[name] = column
    return mapping


def load_extras_prices(path: Path = EXTRAS_PRICES_PATH) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Load ``extras_prices.json``.

    Args:
        path: Location of the JSON file (nested by ``"{Make}_{Model}"``).

    Returns:
        ``(prices, columns)`` where ``columns`` is the ordered union of extra names across
        all segments. A missing file logs a WARNING and returns ``({}, [])``.
    """
    path = Path(path)
    if not path.exists():
        logger.warning("extras_prices.json not found at %s - extras feature disabled", path)
        return {}, []
    with path.open(encoding="utf-8") as f:
        prices: dict[str, dict[str, int]] = json.load(f)
    seen: dict[str, None] = {}
    for segment in prices.values():
        for extra in segment:
            seen[extra] = None
    columns = list(seen)
    logger.info("Loaded extras_prices.json: %d model segment(s), %d unique extra column(s)", len(prices), len(columns))
    return prices, columns


EXTRAS_PRICES, EXTRA_COLUMNS = load_extras_prices()
EXTRA_COLUMN_MAP: dict[str, str] = build_extra_column_map(EXTRA_COLUMNS)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

database = peewee.DatabaseProxy()


class BaseModel(peewee.Model):
    """Base model bound to the module-level database proxy."""

    class Meta:
        database = database


class UtcDateTimeField(peewee.DateTimeField):
    """``timestamptz`` field that always round-trips timezone-aware UTC datetimes."""

    field_type = "TIMESTAMPTZ"

    def db_value(self, value: Any) -> Any:
        """Store a datetime as a UTC ISO-8601 string."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat()
        return value

    def python_value(self, value: Any) -> Any:
        """Return a timezone-aware UTC datetime from a datetime or ISO string."""
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value


class ListingBase(BaseModel):
    """Fixed columns of the ``listing`` table. Never created as a table itself."""

    id = peewee.AutoField()
    ad_key = peewee.TextField(unique=True)
    title = peewee.TextField()
    price_gbp = peewee.IntegerField()
    year = peewee.IntegerField(null=True)
    miles = peewee.IntegerField(null=True)
    fuel_type = peewee.TextField(null=True)
    detail_url = peewee.TextField(null=True)
    make = peewee.TextField()
    model = peewee.TextField(null=True)
    model_type = peewee.TextField(null=True)
    postcode = peewee.TextField()
    extras_total = peewee.IntegerField(default=0)
    extras_other = peewee.TextField(null=True)
    first_found = UtcDateTimeField()
    last_found = UtcDateTimeField()
    days_live = peewee.IntegerField(default=0)


def _build_listing_model() -> type[ListingBase]:
    extra_fields: dict[str, Any] = {
        column: peewee.IntegerField(default=0, constraints=[peewee.SQL("DEFAULT 0")]) for column in EXTRA_COLUMN_MAP.values()
    }
    meta = type("Meta", (), {"table_name": TABLE_NAME})
    return cast("type[ListingBase]", type("Listing", (ListingBase,), {**extra_fields, "Meta": meta}))


Listing: type[ListingBase] = _build_listing_model()


# ---------------------------------------------------------------------------
# Configuration and connection
# ---------------------------------------------------------------------------


def get_database_url(env: Mapping[str, str] | None = None) -> str:
    """Return the validated ``DATABASE_URL``.

    Args:
        env: Mapping to read from (default ``os.environ``).

    Returns:
        The stripped URL.

    Raises:
        ConfigError: If it is missing, blank, or uses an unsupported scheme. The message
            never includes the value.
    """
    source = os.environ if env is None else env
    value = source.get(DATABASE_URL_ENV, "").strip()
    if not value:
        raise ConfigError(f"{DATABASE_URL_ENV} is not set. Copy .env.template to .env and set {DATABASE_URL_ENV}.")
    scheme = urlsplit(value).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise ConfigError(f"{DATABASE_URL_ENV} is invalid: expected a scheme in {', '.join(ALLOWED_URL_SCHEMES)}. See .env.template for the format.")
    return value


def with_default_sslmode(url: str) -> str:
    """Append ``sslmode=require`` to a Postgres URL unless it already sets ``sslmode``.

    Args:
        url: A validated database URL.

    Returns:
        The URL with ``sslmode=require`` added for ``postgres``/``postgresql`` schemes; any
        other URL, or one that already has ``sslmode``, is returned unchanged.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("postgres", "postgresql"):
        return url
    if any(key.lower() == "sslmode" for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return url
    query = f"{parts.query}&sslmode=require" if parts.query else "sslmode=require"
    return urlunsplit(parts._replace(query=query))


def connect_from_env(env: Mapping[str, str] | None = None) -> peewee.Database:
    """Build (but do not open) a Peewee database from ``DATABASE_URL``.

    Args:
        env: Mapping to read from (default ``os.environ``).

    Returns:
        A database with ``autoconnect=False``. The URL's user and password are percent-decoded
        (so ``%21`` is ``!``). Postgres URLs default to ``sslmode=require``
        unless they set ``sslmode`` themselves. The URL is never logged.

    Raises:
        ConfigError: If the URL is missing or invalid.
    """
    url = with_default_sslmode(get_database_url(env))
    try:
        return db_url.connect(url, unquote_user=True, unquote_password=True, autoconnect=False)
    except Exception:
        raise ConfigError(f"{DATABASE_URL_ENV} could not be parsed. See .env.template for the format.") from None


def bind_database(db: peewee.Database) -> None:
    """Point the module-level proxy at *db*."""
    database.initialize(db)


def check_schema() -> None:
    """Read-only check that the live table has every model column.

    Raises:
        SchemaError: If the table or any model column is missing. Orphan columns are allowed.
    """
    if not database.table_exists(TABLE_NAME):
        raise SchemaError(f"Table {TABLE_NAME!r} does not exist. Run `just db-init` to create it.")
    live = {c.name for c in database.get_columns(TABLE_NAME)}
    missing = [f.column_name for f in Listing._meta.sorted_fields if f.column_name not in live]
    if missing:
        raise SchemaError(f"Table {TABLE_NAME!r} is missing column(s): {', '.join(missing)}. Run `just db-init` to add them.")


_HINTS = (
    (
        "could not translate host name",
        "The host does not resolve. Supabase's direct host (db.<ref>.supabase.co) is IPv6-only; "
        "use the transaction pooler URI (aws-N-<region>.pooler.supabase.com:6543, user postgres.<project-ref>).",
    ),
    (
        "tenant or user not found",
        "The pooler rejected the user/host pair. The user must be postgres.<project-ref> and the host the pooler "
        "host for your project's region (copy it from Supabase > Connect).",
    ),
    ("password authentication failed", "Wrong password for this user. Check DATABASE_URL (percent-encode special characters, e.g. ! as %21)."),
    ("timeout expired", "The connection timed out. Check network/firewall access to the host and port."),
    ("connection refused", "Nothing is listening on that host/port. Check the port (pooler transaction mode is 6543)."),
    ("ssl", "SSL negotiation failed. Supabase requires sslmode=require."),
)


def _secrets(database: peewee.Database | None) -> list[str]:
    """Return password-like strings that must never reach the logs."""
    found: list[str] = []
    params = getattr(database, "connect_params", None) or {}
    if params.get("password"):
        found.append(str(params["password"]))
    raw = os.environ.get(DATABASE_URL_ENV, "")
    if raw:
        found.append(raw)
    return [s for s in found if s]


def _redact(text: str, secrets: list[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


def _describe_target(database: peewee.Database | None) -> str:
    """Describe where we tried to connect, without the password."""
    if database is None:
        return "target unknown (config not loaded)"
    params = getattr(database, "connect_params", None) or {}
    return (
        f"{type(database).__name__} host={params.get('host')!r} port={params.get('port')!r} "
        f"user={params.get('user')!r} dbname={database.database!r} sslmode={params.get('sslmode')!r}"
    )


def _hint_for(message: str) -> str | None:
    lowered = message.lower()
    return next((hint for needle, hint in _HINTS if needle in lowered), None)


def describe_database_failure(exc: peewee.DatabaseError, database: peewee.Database | None, *, connecting: bool) -> list[str]:
    """Build log lines for a database failure: cause, target and a hint, never the password.

    Args:
        exc: The Peewee/driver error.
        database: The database that failed, used to describe the target and redact secrets.
        connecting: ``True`` when the failure happened while opening the connection.

    Returns:
        Lines to log at ERROR level, in order.
    """
    message = _redact(str(exc).strip() or "<no message>", _secrets(database))
    outcome = "could not connect" if connecting else "schema sync failed and was rolled back"
    lines = [f"Database {outcome} [{type(exc).__name__}: {message}]", f"Target: {_describe_target(database)}"]
    hint = _hint_for(message)
    if hint:
        lines.append(f"Hint: {hint}")
    return lines


def init_db(db: peewee.Database | None = None) -> peewee.Database:
    """Bind, connect and check the schema. Issues no DDL.

    Args:
        db: Database to use; defaults to ``connect_from_env()``.

    Returns:
        The bound, connected database.

    Raises:
        ConfigError: If ``DATABASE_URL`` is missing or invalid.
        SchemaError: If the table or columns are missing.
        DatabaseUnavailableError: If the connection cannot be opened (message is redacted).
    """
    if db is None:
        db = connect_from_env()
    bind_database(db)
    try:
        db.connect(reuse_if_open=True)
    except peewee.DatabaseError as exc:
        lines = describe_database_failure(exc, db, connecting=True)
        raise DatabaseUnavailableError("\n".join(lines)) from None
    check_schema()
    return db


def close_db() -> None:
    """Close the connection if the proxy is initialised and open. Safe to call twice."""
    if database.obj is not None and not database.is_closed():
        database.close()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def extract_ad_key(detail_url: str | None) -> str | None:
    """Return the 15-digit advert id from the last path segment of *detail_url*, else ``None``."""
    if not detail_url:
        return None
    segments = [s for s in urlsplit(detail_url).path.split("/") if s]
    if not segments:
        return None
    last = segments[-1]
    return last if _AD_KEY_RE.fullmatch(last) else None


def fallback_ad_key(title: str | None, year: int | None, miles: int | None, fuel_type: str | None) -> str:
    """Return a deterministic ``nokey_<sha1>`` key when no advert id is available."""
    parts = ["" if p is None else str(p) for p in (title, year, miles, fuel_type)]
    # sha1 is a stable, non-security identity hash mandated by the contract (nokey_ + 40 hex).
    digest = hashlib.sha1("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()  # nosemgrep: insecure-hash-algorithm-sha1
    return FALLBACK_KEY_PREFIX + digest


def parse_price_gbp(text: str | None) -> int | None:
    """Parse ``"£42,000"`` or ``"42000"`` to an int; ``None`` if unparseable."""
    if text is None:
        return None
    cleaned = re.sub(r"[£,\s]", "", text)
    return int(cleaned) if cleaned.isdigit() else None


def parse_year(text: str | None) -> int | None:
    """Return the first four-digit year (1900-2099) in *text*, else ``None``."""
    if not text:
        return None
    match = re.search(r"\b(?:19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


def parse_miles(text: str | None) -> int | None:
    """Parse ``"12,345 miles"`` to an int; ``None`` for ``"NA"`` or missing."""
    if not text:
        return None
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def backoff_seconds(attempt: int) -> float:
    """Return the sleep after failed *attempt* (1-based), capped at ``MAX_BACKOFF_SECONDS``."""
    return min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2 ** (attempt - 1))


def append_error_log(message: str, path: Path | str = ERROR_LOG_PATH) -> None:
    """Append one UTC-timestamped line to the error log."""
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(f"{utc_now().isoformat()} {message}\n")


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    """Return *value* stripped, or ``None`` for empty or ``"NA"``."""
    if value is None:
        return None
    value = value.strip()
    return None if value in ("", "NA") else value


def _normalise_row(row: ListingRow) -> dict[str, Any] | None:
    """Turn a scraped row into model field values, or ``None`` if it must be skipped."""
    title = (row.get("title") or "").strip()
    price = parse_price_gbp(row.get("price"))
    if not title:
        logger.warning("Skipping listing with no title (detail_url=%s)", row.get("detail_url"))
        return None
    if price is None:
        logger.warning("Skipping listing %r: unparseable price %r", title, row.get("price"))
        return None

    year = parse_year(row.get("year"))
    miles = parse_miles(row.get("miles"))
    fuel_type = _clean(row.get("fuel_type"))
    detail_url = row.get("detail_url") or None

    ad_key = extract_ad_key(detail_url)
    if ad_key is None:
        ad_key = fallback_ad_key(title, year, miles, fuel_type)
        logger.warning("No advert id in detail_url for %r; using fallback key %s", title, ad_key)

    extras = dict.fromkeys(EXTRA_COLUMN_MAP.values(), 0)
    for name, amount in (row.get("extras") or {}).items():
        column = EXTRA_COLUMN_MAP.get(name)
        if column is None:
            logger.warning("Ignoring unknown extra %r for listing %r", name, title)
            continue
        extras[column] = int(amount)

    return {
        "ad_key": ad_key,
        "title": title,
        "price_gbp": price,
        "year": year,
        "miles": miles,
        "fuel_type": fuel_type,
        "detail_url": detail_url,
        "make": row["make"],
        "model": _clean(row.get("model")),
        "model_type": _clean(row.get("model_type")),
        "postcode": row["postcode"],
        "extras_total": sum(extras.values()),
        "extras_other": _clean(row.get("extras_other")),
        **extras,
    }


def _write_batch(batch: list[dict[str, Any]], now: datetime) -> int:
    """Upsert *batch* inside one transaction; return the number of rows written."""
    saved = 0
    with database.atomic():
        for fields in batch:
            existing = Listing.get_or_none(Listing.ad_key == fields["ad_key"])
            if existing is None:
                Listing.create(**fields, first_found=now, last_found=now, days_live=0)
            else:
                days_live = (now - existing.first_found).days
                Listing.update(**fields, last_found=now, days_live=days_live).where(Listing.id == existing.id).execute()
            saved += 1
    return saved


def save_listings(
    rows: Sequence[ListingRow],
    *,
    clock: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    error_log_path: Path | str = ERROR_LOG_PATH,
) -> SaveResult:
    """Upsert one page of listings on ``ad_key``, retrying on connection errors.

    Args:
        rows: Scraped listings for one page.
        clock: Returns the current UTC time; read once per attempt.
        sleep: Called with the backoff between attempts.
        error_log_path: File that receives a line when retries are exhausted.

    Returns:
        A ``SaveResult``. When every attempt fails with a retryable error the failure is logged
        and returned (``succeeded=False``); it is not raised so the run can continue.
        Non-retryable exceptions propagate.
    """
    if not rows:
        return SaveResult(True, 0, 0, 0)

    batch: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        fields = _normalise_row(row)
        if fields is None:
            skipped += 1
        else:
            batch.append(fields)
    if not batch:
        return SaveResult(True, 0, 0, skipped)

    for attempt in range(1, MAX_DB_ATTEMPTS + 1):
        try:
            if attempt > 1:
                database.close()
                database.connect(reuse_if_open=True)
            saved = _write_batch(batch, clock())
        except RETRYABLE_EXCEPTIONS as exc:
            logger.warning("Database write failed on attempt %d/%d: %s", attempt, MAX_DB_ATTEMPTS, type(exc).__name__)
            if attempt < MAX_DB_ATTEMPTS:
                sleep(backoff_seconds(attempt))
                continue
            logger.exception("Database write failed after %d attempts; skipping %d listing(s)", MAX_DB_ATTEMPTS, len(batch))
            append_error_log(f"save_listings gave up after {MAX_DB_ATTEMPTS} attempts: {type(exc).__name__}: {exc}", error_log_path)
            return SaveResult(False, MAX_DB_ATTEMPTS, 0, skipped)
        if attempt > 1:
            logger.info("Database write succeeded after retry: retry_count=%d", attempt - 1)
        return SaveResult(True, attempt, saved, skipped)

    raise AssertionError("unreachable")  # pragma: no cover
