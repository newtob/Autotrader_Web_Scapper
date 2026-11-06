"""make.json validation: every entry needs a make and model and an aggregatedTrim key (empty allowed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import make_config
from make_config import MakeConfigError, load_make_config

GOOD = {"displayName": "Porsche", "uriValue": "Porsche", "model": "Taycan", "fuelType": "Electric", "aggregatedTrim": "4S", "price-to": 42000}


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "make.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_entry_is_parsed(tmp_path: Path) -> None:
    (entry,) = load_make_config(_write(tmp_path, [GOOD]))
    assert (entry.make, entry.display_name, entry.model, entry.fuel_type, entry.trim, entry.price_to) == (
        "Porsche",
        "Porsche",
        "Taycan",
        "Electric",
        "4S",
        "42000",
    )


def test_empty_trim_is_allowed_and_optional_fields_default(tmp_path: Path) -> None:
    (entry,) = load_make_config(_write(tmp_path, [{"uriValue": "Hyundai", "model": "Ioniq 5", "aggregatedTrim": ""}]))
    assert (entry.display_name, entry.trim, entry.fuel_type, entry.price_to) == ("Hyundai", "", "", "")


@pytest.mark.parametrize("missing", ["uriValue", "model", "aggregatedTrim"])
def test_missing_required_key_is_rejected(tmp_path: Path, missing: str) -> None:
    entry = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(MakeConfigError, match=f"entry 0.*{missing}"):
        load_make_config(_write(tmp_path, [entry]))


@pytest.mark.parametrize("field", ["uriValue", "model"])
def test_blank_required_value_is_rejected(tmp_path: Path, field: str) -> None:
    with pytest.raises(MakeConfigError, match=field):
        load_make_config(_write(tmp_path, [{**GOOD, field: "   "}]))


def test_duplicates_are_rejected_case_insensitively(tmp_path: Path) -> None:
    with pytest.raises(MakeConfigError, match="duplicates entry 0"):
        load_make_config(_write(tmp_path, [GOOD, {**GOOD, "uriValue": "porsche", "aggregatedTrim": "4s"}]))


def test_same_model_with_different_trim_is_not_a_duplicate(tmp_path: Path) -> None:
    assert len(load_make_config(_write(tmp_path, [GOOD, {**GOOD, "aggregatedTrim": "GTS"}]))) == 2


@pytest.mark.parametrize("price", [0, -5, "abc", True, 1.5])
def test_bad_price_to_is_rejected(tmp_path: Path, price: object) -> None:
    with pytest.raises(MakeConfigError, match="price-to"):
        load_make_config(_write(tmp_path, [{**GOOD, "price-to": price}]))


@pytest.mark.parametrize("data", [{}, [], ["x"]])
def test_non_list_or_non_object_entries_are_rejected(tmp_path: Path, data: object) -> None:
    with pytest.raises(MakeConfigError):
        load_make_config(_write(tmp_path, data))


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(MakeConfigError, match="Cannot read"):
        load_make_config(tmp_path / "nope.json")


def test_committed_make_json_is_valid() -> None:
    assert load_make_config(make_config.MAKE_JSON_PATH)
