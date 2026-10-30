"""Reseller category presets for OLX (iPhone, laptops, desktops, consoles, graphics cards).

OLX category ids are learned once on the owner's computer by ``scripts/olx_categories.py``
(it asks OLX which category the typical results of a query belong to) and stored in
``app/data/olx_categories.json``. With ids known, the bot asks OLX only for that category
(``category_id`` parameter) — fewer requests, no accessories from other sections.
Without the file the preset still works: the query is sent without a category filter and the
matcher keeps only listings whose category id is known for the preset (or all, if none known).
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "olx_categories.json"

# preset code -> sample queries used to discover the OLX category ids
PRESETS: dict[str, list[str]] = {
    "iphone": ["iphone 15 pro", "iphone 13"],
    "laptopy": ["laptop lenovo", "macbook air"],
    "komputery": ["komputer stacjonarny", "komputer gamingowy"],
    "konsole": ["konsola playstation 5", "konsola xbox series x"],
    "karty_graficzne": ["karta graficzna rtx", "rtx 3070"],
}


def load_ids(path: Path = DATA_FILE) -> dict[str, list[int]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {code: [int(i) for i in ids] for code, ids in data.items() if code in PRESETS and isinstance(ids, list)}


class OlxCategories:
    def __init__(self, ids: dict[str, list[int]] | None = None) -> None:
        self.ids = load_ids() if ids is None else ids
        self.reverse = {cid: code for code, ids in self.ids.items() for cid in ids}

    def category_ids(self, preset: str | None) -> list[int]:
        return list(self.ids.get(preset or "", []))

    def preset_of(self, category_id: int | None) -> str | None:
        return self.reverse.get(category_id) if category_id is not None else None
