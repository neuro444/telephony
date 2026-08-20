"""Generate the SPEECH_HINTS value from a menu JSON file.

Plivo's speech recognition mangles domain terms like "gulab jamun" or
"biriyani" with its default model. Feeding item names in as hints is the
cheapest accuracy win available (docs: <GetInput hints="...">).

Usage:
    python scripts/generate_hints.py path/to/menu_flat.json
    python scripts/generate_hints.py path/to/menu_flat.json --debug

Prints a single line ready to paste into .env as SPEECH_HINTS=...
Plivo's documented limits: 500 phrases / 10,000 characters max.

Handles several menu shapes:
  1. A flat list: ["Item A", "Item B", ...] or [{"name": "Item A", ...}, ...]
  2. A dict of category -> [items]: {"Cakes": ["Black Forest", ...], ...}
  3. A "flat rows" / CSV-style shape: {"fields": ["name","price",...],
     "rows": [["Black Forest", 350, ...], ...]}, with the field list used
     to find which column is "name". This is chat_manager's own menu
     format (see menu_is_sent_as_csv_not_json in its test suite).
"""
import json
import sys

MAX_PHRASES = 500
MAX_CHARS = 10_000


def _names_from_flat_rows(data: dict) -> list[str] | None:
    """Handle {"fields": [...], "rows": [[...], ...]} shape. Returns None
    if the dict doesn't actually look like this shape."""
    fields = data.get("fields")
    rows = data.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return None
    try:
        name_idx = [str(f).lower() for f in fields].index("name")
    except ValueError:
        return None
    names = []
    for row in rows:
        if isinstance(row, list) and name_idx < len(row):
            names.append(str(row[name_idx]))
        elif isinstance(row, dict) and "name" in row:
            names.append(str(row["name"]))
    return names


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

    # Shape 3: flat rows / CSV-style (chat_manager's own format).
    if isinstance(data, dict):
        flat_names = _names_from_flat_rows(data)
        if flat_names is not None:
            if debug:
                print(f"[debug] detected fields/rows shape, {len(flat_names)} rows", file=sys.stderr)
            return _dedupe(flat_names)

    names: list[str] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Shape 2: category -> [items]. Only descend into values that are
        # actually lists -- skip stray strings/numbers at the top level
        # (e.g. a restaurant name or version field) so we don't iterate
        # over a string character-by-character.
        items = []
        for value in data.values():
            if isinstance(value, list):
                items.extend(value)
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
    seen: set[str] = set()
    unique = []
    for n in names:
        n = n.strip()
        if n and n not in seen:
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
