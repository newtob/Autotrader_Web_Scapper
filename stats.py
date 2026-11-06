"""Daily average-price statistics (``daily_stats``): compute today's snapshot and backfill history.

Pure aggregation over ``Listing`` rows; persistence is ``db.save_daily_stats``. Nothing here
connects to the database itself or loads ``.env``; entrypoints do that.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Collection, Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import db

logger = logging.getLogger("autotrader.stats")

MAKE_JSON_PATH = Path(__file__).resolve().parent / "make.json"

SearchKey = tuple[str, str, str]  # (make, model, model_type); '' rather than NULL


def _round_half_up(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _summarise(stat_date: date, key: SearchKey, cars: Collection[tuple[int, int]]) -> db.DailyStatRow:
    """Build a row from ``(price_gbp, days_live)`` pairs; averages are None when there are no cars."""
    make, model, model_type = key
    count = len(cars)
    avg_price = avg_days = None
    if count:
        avg_price = int(_round_half_up(Decimal(sum(p for p, _ in cars)) / count, "1"))
        avg_days = _round_half_up(Decimal(sum(d for _, d in cars)) / count, "0.1")
    return {
        "stat_date": stat_date,
        "make": make,
        "model": model,
        "model_type": model_type,
        "car_count": count,
        "avg_price_gbp": avg_price,
        "avg_days_live": avg_days,
    }


def load_searches(path: Path = MAKE_JSON_PATH) -> list[SearchKey]:
    """Return every ``(make, model, model_type)`` tracked in ``make.json``."""
    with Path(path).open(encoding="utf-8") as f:
        return [(e["uriValue"], e.get("model", ""), e.get("aggregatedTrim", "")) for e in json.load(f)]


def _listing_key(row: db.ListingBase) -> SearchKey:
    return (row.make, row.model or "", row.model_type or "")


def compute_daily_stats(
    run_date: date,
    *,
    searches: Iterable[SearchKey] | None = None,
    skip: Collection[SearchKey] = (),
) -> list[db.DailyStatRow]:
    """Aggregate the listings seen on *run_date* (UTC) per search.

    Args:
        run_date: UTC calendar day; a car counts when its ``last_found`` falls on it.
        searches: Searches that always get a row (``car_count=0`` when no cars). Defaults to ``make.json``.
        skip: Searches that failed before saving anything; they get no row, so a failed scrape
            cannot masquerade as an empty market.

    Returns:
        One row per search, plus one per listing group not in *searches*.
    """
    start = datetime.combine(run_date, time.min, tzinfo=UTC)
    seen = db.Listing.select().where((db.Listing.last_found >= start) & (db.Listing.last_found < start + timedelta(days=1)))
    groups: dict[SearchKey, list[tuple[int, int]]] = defaultdict(list)
    for row in seen:
        groups[_listing_key(row)].append((row.price_gbp, row.days_live))
    for key in load_searches() if searches is None else searches:
        groups.setdefault(key, [])
    skipped = set(skip)
    return [_summarise(run_date, key, cars) for key, cars in sorted(groups.items()) if key not in skipped or cars]


def backfill_daily_stats() -> list[db.DailyStatRow]:
    """Compute a row per day and search for every day captured in ``listing``.

    A listing counts on each UTC day from ``first_found`` to ``last_found`` inclusive, at its
    latest price (history is not stored, so past averages are approximate). ``days_live`` is the
    day's offset from ``first_found``. Days run from the earliest ``first_found`` to the latest
    ``last_found``; searches with no cars on a day get no row (only the live run writes zero rows).
    """
    per_day: dict[date, dict[SearchKey, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for row in db.Listing.select(
        db.Listing.make, db.Listing.model, db.Listing.model_type, db.Listing.price_gbp, db.Listing.first_found, db.Listing.last_found
    ):
        first, last = row.first_found.astimezone(UTC).date(), row.last_found.astimezone(UTC).date()
        for offset in range((last - first).days + 1):
            per_day[first + timedelta(days=offset)][_listing_key(row)].append((row.price_gbp, offset))
    return [_summarise(day, key, cars) for day, groups in sorted(per_day.items()) for key, cars in sorted(groups.items())]
