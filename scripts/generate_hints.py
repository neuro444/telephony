"""Generate the SPEECH_HINTS value from a menu JSON file.

Plivo's speech recognition mangles domain terms like "gulab jamun" or
"biriyani" with its default model. Feeding item names in as hints is the
cheapest accuracy win available (docs: <GetInput hints="...">).

Usage:
    python scripts/generate_hints.py path/to/menu_flat.json
    python scripts/generate_hints.py path/to/menu_flat.json --debug

Prints a single line ready to paste into .env as SPEECH_HINTS=...
Plivo's documented limits: 500 phrases / 10,000 characters max.

Handles chat_manager's real menu shape first, since that's the actual
source of truth this script is run against:
  {"menu_item_fields": ["name", "price", ...], "menu_items": [[...], ...]}
with menu_item_fields giving the column order for each positional row in
menu_items. Falls back to more generic shapes (a flat list, or a dict of
category -> [items]) for any other menu file.
"""
import json
import re
import sys

# Shared list deliberately capped at 100, matching the current Deepgram adapter.
MAX_PHRASES = 100
MAX_CHARS = 10_000

# Canonical menu spellings, ordered by the observed recognition problem first.
# Only names actually present in the supplied menu are included. Do not boost
# corrupt transcripts such as "TV per rota", or invent unavailable dishes.
PRIORITY_NAMES = [
    "Kizhi Porotta", "Beef Kizhi Porotta", "Porotta", "Chicken Egg Kothu Porotta",
    "Kallappam", "Malabar Chicken Biriyani", "Malabar Goat Biriyani",
    "Malabar Fish Biriyani", "Kizhi Biriyani", "Paragon Chicken Biriyani (Kaima Rice)",
    "Beef Perattu", "Beef Ullarthu", "Kerala Beef Fry", "Beef Roast",
    "Fish Pollichathu", "Kethal Chicken Fry", "Karivepila Chicken",
    "Gobi Kondattam", "Chicken Kondattam", "Kandari Chicken Fry",
    "Kandari Chicken Masala", "Kandari Tawa Fish", "Kandari Paneer",
    "Chicken Mappas", "Fish Mappas", "Fish Moilee", "Achayan's Fish Curry (King Fish)",
    "Alleppy Shrimp Curry", "Malabar Chicken Curry", "Goat Malabar Curry",
    "Malabar Veg Kuruma", "Kerala Black Chana Curry", "Egg Roast", "Shrimp Roast",
    "Chicken Pepper Roast", "Goat Pepper Roast", "Beef Pepper Masala",
    "Beef Chettinadu Curry", "Chicken Chettinad Fry", "Goat Chettinad Fry",
    "Chettinad Veg Curry", "Chettinad Chicken Biriyani", "Chettinad Goat Biriyani",
    "Thattu Dosa with Egg Omlette", "Tattu Dosa (2 piece)",
    "Idly (3) with Sambar and Chutney", "Ghee Roast", "Podi Dosa",
    "Bengaluru Ghee Podi Dosa", "Onion Rawa Dosa", "Dilkush", "Coconut Bun",
]


def prioritize_names(names: list[str]) -> list[str]:
    """Put Kerala/nearby regional vocabulary ahead of generic English items.

    This is recognition priority, not a claim that every dish originates in
    Kerala. Keep the complete menu and stable order within each later tier.
    """
    ranks = {name.casefold(): index for index, name in enumerate(PRIORITY_NAMES)}
    regional = re.compile(
        r'kizhi|porotta|kothu|kallappam|malabar|kerala|kondattam|kandari|'
        r'karivepila|pollichathu|kethal|mappas|moilee|achayan|alleppy|perattu|ullarthu|'
        r'chettinad|biriyani|dosa|idly|sambar|kuruma|dilkush', re.I)
    indian = re.compile(
        r'samosa|gobi|paneer|mirchi|pakoda|chole|bhature|dal|channa|'
        r'vada|pav|dabeli|bhaji|puri|chaat|tikki|pappadi|bhel|kachori|'
        r'naan|kulfi|chai|lassi', re.I)
    def rank(name):
        key = name.casefold()
        if key in ranks:
            return (0, ranks[key])
        if regional.search(name):
            return (1, 0)
        if indian.search(name):
            return (2, 0)
        return (3, 0)
    return sorted(_dedupe(names), key=rank)


def extract_names(menu_path: str, debug: bool = False) -> list[str]:
    with open(menu_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if debug:
        if isinstance(data, dict):
            print(f"[debug] top-level dict, keys: {list(data.keys())}", file=sys.stderr)
        elif isinstance(data, list):
            print(f"[debug] top-level list, {len(data)} items", file=sys.stderr)
            if data:
                print(f"[debug] first item: {data[0]!r}", file=sys.stderr)

    names: list[str] = []

    # chat_manager's real menu_flat.json shape: rows are positional arrays
    # whose column order is given by menu_item_fields. Checked first,
    # because the generic dict branch below would otherwise iterate the
    # CHARACTERS of the restaurant_name string and emit single-letter
    # "hints" -- silently producing garbage is worse than failing.
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
        if debug:
            print(f"[debug] detected menu_item_fields/menu_items shape, {len(names)} rows", file=sys.stderr)
        return _dedupe(names)

    # Other reasonable shapes: a flat list of items, or category -> [items].
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Only descend into values that are actually lists -- skip stray
        # top-level strings/numbers (e.g. a restaurant name field).
        items = [
            item
            for group in data.values()
            if isinstance(group, list)
            for item in group
        ]
    else:
        raise ValueError(f"Unrecognized menu file shape: top-level {type(data).__name__}")

    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and "name" in item:
            names.append(str(item["name"]))
        elif isinstance(item, list) and item:
            # A row-shaped list with no declared fields -- assume first
            # column is the name, better than silently dropping it.
            names.append(str(item[0]))

    return _dedupe(names)


def _dedupe(names: list[str]) -> list[str]:
    """Drop duplicates, preserve menu order."""
    seen: set[str] = set()
    unique = []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            unique.append(n)
    return unique


def build_hints(names: list[str]) -> str:
    hints = prioritize_names(names)[:MAX_PHRASES]
    joined = ", ".join(hints)
    while len(joined) > MAX_CHARS and hints:
        hints.pop()
        joined = ", ".join(hints)
    return joined


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--debug"]
    debug = "--debug" in sys.argv
    if len(args) != 1:
        print("Usage: python scripts/generate_hints.py path/to/menu_flat.json [--debug]")
        sys.exit(1)
    names = extract_names(args[0], debug=debug)
    if not names:
        print("No item names found -- run with --debug to see the file's actual shape.")
        sys.exit(1)
    hints = build_hints(names)
    print(f"Generated {len(hints.split(', '))} hint phrases, {len(hints)} chars")
    print()
    print(f"SPEECH_HINTS={hints}")
