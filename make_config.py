"""Load and validate ``make.json``, the list of vehicles to scrape and track.

Every entry needs a ``uriValue`` (make) and a ``model``, and must carry an ``aggregatedTrim`` key (an
empty string means "all trims"). Validation happens once, at startup, so a bad entry fails the run with a
clear message instead of silently scraping the wrong search.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

MAKE_JSON_PATH = Path(__file__).resolve().parent / "make.json"


class MakeConfigError(RuntimeError):
    """Raised when ``make.json`` is unreadable or an entry is invalid."""


class SearchEntry(NamedTuple):
    """One vehicle search. ``trim`` is ``''`` when all trims are wanted."""

    make: str
    display_name: str
    model: str
    fuel_type: str
    trim: str
    price_to: str


def _text(entry: dict[str, Any], key: str, index: int, *, required: bool) -> str:
    if key not in entry:
        if required:
            raise MakeConfigError(f"make.json entry {index}: missing required field {key!r}")
        return ""
    value = entry[key]
    if not isinstance(value, str):
        raise MakeConfigError(f"make.json entry {index}: {key!r} must be a string")
    return value.strip()


def _parse_entry(entry: object, index: int) -> SearchEntry:
    if not isinstance(entry, dict):
        raise MakeConfigError(f"make.json entry {index}: must be an object")
    make = _text(entry, "uriValue", index, required=True)
    model = _text(entry, "model", index, required=True)
    if not make:
        raise MakeConfigError(f"make.json entry {index}: 'uriValue' must not be blank")
    if not model:
        raise MakeConfigError(f"make.json entry {index}: 'model' must not be blank")
    trim = _text(entry, "aggregatedTrim", index, required=True)  # '' is allowed (all trims)
    price_to = ""
    if entry.get("price-to") not in (None, ""):
        value = entry["price-to"]
        if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).isdecimal() or int(value) <= 0:
            raise MakeConfigError(f"make.json entry {index}: 'price-to' must be a positive integer")
        price_to = str(int(value))
    return SearchEntry(
        make=make,
        display_name=_text(entry, "displayName", index, required=False) or make,
        model=model,
        fuel_type=_text(entry, "fuelType", index, required=False),
        trim=trim,
        price_to=price_to,
    )


def load_make_config(path: Path | str = MAKE_JSON_PATH) -> list[SearchEntry]:
    """Return the validated searches in file order.

    Raises:
        MakeConfigError: If the file is missing, not a JSON list, or an entry is invalid or duplicated
            (make, model and trim are compared case-insensitively).
    """
    try:
        with Path(path).open(encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        raise MakeConfigError(f"Cannot read {Path(path).name}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise MakeConfigError(f"{Path(path).name} must be a non-empty JSON list")

    entries: list[SearchEntry] = []
    seen: dict[tuple[str, str, str], int] = {}
    for index, item in enumerate(raw):
        entry = _parse_entry(item, index)
        key = (entry.make.lower(), entry.model.lower(), entry.trim.lower())
        if key in seen:
            raise MakeConfigError(f"make.json entry {index}: duplicates entry {seen[key]} ({entry.make} {entry.model} {entry.trim!r})")
        seen[key] = index
        entries.append(entry)
    return entries
