"""Generate the SPEECH_HINTS value from menu/menu_flat.json.

Plivo's speech recognition mangles domain terms like "gulab jamun" or
"biriyani" with its default model. Feeding item names in as hints is the
cheapest accuracy win available (docs: <GetInput hints="...">).

Usage:
    python scripts/generate_hints.py path/to/menu_flat.json

Prints a single line ready to paste into .env as SPEECH_HINTS=...
Plivo's documented limits: 500 phrases / 10,000 characters max.
"""
import json
import sys

MAX_PHRASES = 500
MAX_CHARS = 10_000


def extract_names(menu_path: str) -> list[str]:
    with open(menu_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    names: list[str] = []

    # The real menu_flat.json shape: rows are positional arrays whose column
    # order is given by menu_item_fields. Checked first, because the generic
    # dict branch below would otherwise iterate the CHARACTERS of the
    # restaurant_name string and emit 47 single-letter "hints" — silently
    # producing garbage is worse than failing.
    if isinstance(data, dict) and "menu_items" in data:
        fields = data.get("menu_item_fields") or []
        try:
            name_idx = fields.index("name")
        except ValueError:
            name_idx = 0
        for row in data["menu_items"]:
            if isinstance(row, (list, tuple)) and len(row) > name_idx:
                names.append(str(row[name_idx]))
            elif isinstance(row, dict) and "name" in row:
                names.append(str(row["name"]))
            elif isinstance(row, str):
                names.append(row)
        return _dedupe(names)

    # Other reasonable shapes: a flat list of items, or category -> [items].
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [
            item
            for group in data.values()
            if isinstance(group, list)
            for item in group
        ]
    else:
        raise ValueError("Unrecognized menu_flat.json shape")

    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and "name" in item:
            names.append(str(item["name"]))
    return _dedupe(names)


def _dedupe(names: list[str]) -> list[str]:
    """Drop duplicates, preserve menu order."""

    seen: set[str] = set()
    unique = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique


def build_hints(names: list[str]) -> str:
    hints = names[:MAX_PHRASES]
    joined = ", ".join(hints)
    while len(joined) > MAX_CHARS and hints:
        hints.pop()
        joined = ", ".join(hints)
    return joined


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/generate_hints.py path/to/menu_flat.json")
        sys.exit(1)
    names = extract_names(sys.argv[1])
    hints = build_hints(names)
    print(f"Generated {len(hints.split(', '))} hint phrases, {len(hints)} chars")
    print()
    print(f"SPEECH_HINTS={hints}")
