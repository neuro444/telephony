"""Speech hints generation.

Plivo's ASR mangles "Gobi Kondattam" without hints. Getting these wrong is
silent — the previous version emitted 47 single-character "hints" from the
restaurant name and would have shipped.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from generate_hints import build_hints, extract_names  # noqa: E402

REAL_SHAPE = {
    "restaurant_name": "Cake World, Alpharetta",
    "menu_item_fields": ["name", "price", "category", "is_vegetarian"],
    "menu_items": [
        ["Samosa", 5.99, "veg_appetizer", True],
        ["Gobi Kondattam", 11.99, "veg_appetizer", True],
        ["Samosa", 5.99, "veg_appetizer", True],
    ],
}


def write(tmp_path, data):
    p = tmp_path / "menu_flat.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_reads_positional_rows_using_menu_item_fields(tmp_path):
    names = extract_names(write(tmp_path, REAL_SHAPE))
    assert names == ["Samosa", "Gobi Kondattam"]  # deduped, order preserved


def test_does_not_iterate_the_restaurant_name(tmp_path):
    """The bug this test exists for: 'Cake World' became ['C','a','k','e'...]."""
    names = extract_names(write(tmp_path, REAL_SHAPE))
    assert all(len(n) > 1 for n in names)
    assert "C" not in names


def test_name_column_is_located_not_assumed(tmp_path):
    """A reordered menu_item_fields must still find the name."""
    data = {
        "menu_item_fields": ["price", "name", "category"],
        "menu_items": [[5.99, "Samosa", "veg"]],
    }
    assert extract_names(write(tmp_path, data)) == ["Samosa"]


def test_accepts_a_flat_list_of_objects(tmp_path):
    data = [{"name": "Samosa"}, {"name": "Idly"}]
    assert extract_names(write(tmp_path, data)) == ["Samosa", "Idly"]


def test_hints_are_comma_separated(tmp_path):
    hints = build_hints(extract_names(write(tmp_path, REAL_SHAPE)))
    assert hints == "Samosa, Gobi Kondattam"


def test_respects_plivos_documented_limits():
    """Plivo caps hints at 500 phrases / 10,000 characters."""
    hints = build_hints([f"Item Number {i}" for i in range(2000)])
    assert len(hints) <= 10_000
    assert len(hints.split(", ")) <= 500


def test_real_menu_file_yields_real_items():
    """Guards against the live menu drifting into a shape we mis-parse."""
    real = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "chat_manager_repo", "menu", "menu_flat.json")
    if not os.path.exists(real):
        pytest.skip("chat_manager_repo not checked out alongside")
    names = extract_names(real)
    assert len(names) > 100
    assert "Samosa" in names
    assert all(len(n) > 1 for n in names)
