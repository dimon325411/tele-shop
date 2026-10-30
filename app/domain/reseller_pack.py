"""Ready-made reseller searches ("pakiet") created with one command (/pack).

Every search is in hot-deal mode: a notification only for a fresh listing at least ``DEAL_PERCENT`` %
below the median of the same model/storage/condition. Price floors only cut obvious accessories
and "1 zł" placeholders; they are NOT market estimates (the median is computed from live data).
OLX load: one request per search (per category id) every ``INTERVAL`` seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

DEAL_PERCENT = 15
INTERVAL = 15 * 60


@dataclass(frozen=True)
class PackItem:
    name: str
    category: str  # preset code from app/sources/olx_categories.py
    keywords: list[str]
    excluded: list[str] = field(default_factory=list)
    price_min: int | None = None
    alt: list[str] = field(default_factory=list)  # any one of these must be present


PACK: list[PackItem] = [
    # Excluded phrases are checked in the title AND the description, so they are precise model names
    # ("15 plus"), never single common words ("plus etui", "w zestawie pad" must still pass).
    PackItem("iPhone 13", "iphone", ["iphone 13"], ["13 pro", "13 mini"], 600),
    PackItem("iPhone 13 Pro", "iphone", ["iphone 13 pro"], ["pro max"], 900),
    PackItem("iPhone 14 Pro", "iphone", ["iphone 14 pro"], ["pro max"], 1300),
    PackItem("iPhone 15", "iphone", ["iphone 15"], ["15 pro", "15 plus"], 1200),
    PackItem("iPhone 15 Pro", "iphone", ["iphone 15 pro"], ["pro max"], 1600),
    PackItem("iPhone 15 Pro Max", "iphone", ["iphone 15 pro max"], [], 2000),
    PackItem("iPhone 16 Pro", "iphone", ["iphone 16 pro"], ["pro max"], 2500),
    PackItem("PlayStation 5", "konsole", [], ["ps5 pro", "ps5 portal", "playstation 5 pro"], 900,
             alt=["ps5", "playstation 5"]),
    PackItem("Xbox Series X", "konsole", ["xbox series x"], [], 800),
    PackItem("RTX 3070", "karty_graficzne", ["rtx 3070"], ["3070 ti"], 700),
    PackItem("RTX 3080", "karty_graficzne", ["rtx 3080"], ["3080 ti"], 1100),
    PackItem("RTX 4060 Ti", "karty_graficzne", ["rtx 4060 ti"], [], 900),
    PackItem("RTX 4070", "karty_graficzne", ["rtx 4070"], ["4070 ti", "4070 super"], 1300),
    PackItem("MacBook Air M1", "laptopy", ["macbook air", "m1"], [], 1200),
    PackItem("MacBook Air M2", "laptopy", ["macbook air", "m2"], [], 2000),
    PackItem("MacBook Pro M1", "laptopy", ["macbook pro", "m1"], ["m1 max"], 1600),
]


def draft_kwargs(item: PackItem) -> dict:
    """Arguments for SearchDraft (kept here so the domain stays free of service imports)."""
    return dict(name=item.name, sources=["olx"], keywords=list(item.keywords), alt_keywords=list(item.alt),
                excluded_keywords=list(item.excluded), category=item.category,
                price_min=Decimal(item.price_min) if item.price_min else None, condition="used",
                deal_min_discount=DEAL_PERCENT, interval_seconds=INTERVAL)
