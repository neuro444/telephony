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
    # Tolerant of a few reasonable shapes: a flat list of items, or a dict
    # of category -> [items]. Each item may be a string or an object with
    # a "name" field.
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [item for group in data.values() for item in group]
    else:
        raise ValueError("Unrecognized menu_flat.json shape")

    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and "name" in item:
            names.append(str(item["name"]))

    # Dedupe, preserve order.
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
