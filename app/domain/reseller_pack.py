"""Ready-made reseller searches ("pakiet") created with one command (/pack).

Every search is in hot-deal mode: a notification only for a fresh listing at least ``DEAL_PERCENT`` %
below the median of the same model/storage/condition. Price floors only cut obvious accessories
and "1 zł" placeholders; they are NOT market estimates (the median is computed from live data).
OLX load: one request per search (per category id) every ``INTERVAL`` seconds; checks run one at a time
(>=10 s between requests), so ~55 requests per round take ~9-10 minutes.
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


G = "karty_graficzne"

PACK: list[PackItem] = [
    # Excluded phrases are checked in the title AND the description, so they are precise model names
    # ("15 plus"), never single common words ("plus etui", "w zestawie pad" must still pass).
    # Number tokens match exactly: "iphone 16" never matches "16e"; "rtx 4060" never matches "4060ti".
    # --- iPhone (from the 15 series)
    PackItem("iPhone 15", "iphone", ["iphone 15"], ["15 pro", "15 plus"], 1000),
    PackItem("iPhone 15 Plus", "iphone", ["iphone 15 plus"], [], 1100),
    PackItem("iPhone 15 Pro", "iphone", ["iphone 15 pro"], ["pro max"], 1500),
    PackItem("iPhone 15 Pro Max", "iphone", ["iphone 15 pro max"], [], 1800),
    PackItem("iPhone 16", "iphone", ["iphone 16"], ["16 pro", "16 plus"], 1400),
    PackItem("iPhone 16 Plus", "iphone", ["iphone 16 plus"], [], 1600),
    PackItem("iPhone 16 Pro", "iphone", ["iphone 16 pro"], ["pro max"], 2200),
    PackItem("iPhone 16 Pro Max", "iphone", ["iphone 16 pro max"], [], 2600),
    PackItem("iPhone 16e", "iphone", ["iphone 16e"], [], 1100),
    PackItem("iPhone 17", "iphone", ["iphone 17"], ["17 pro", "17e"], 1900),
    PackItem("iPhone 17 Pro", "iphone", ["iphone 17 pro"], ["pro max"], 2800),
    PackItem("iPhone 17 Pro Max", "iphone", ["iphone 17 pro max"], [], 3200),
    PackItem("iPhone 17e", "iphone", ["iphone 17e"], [], 1300),
    PackItem("iPhone Air", "iphone", ["iphone air"], [], 2000),
    PackItem("iPhone 18 Pro", "iphone", ["iphone 18 pro"], ["pro max"], 3200),
    PackItem("iPhone 18 Pro Max", "iphone", ["iphone 18 pro max"], [], 3600),
    # --- consoles
    PackItem("PlayStation 5", "konsole", [], ["ps5 pro", "ps5 portal", "playstation 5 pro"], 800,
             alt=["ps5", "playstation 5"]),
    PackItem("PlayStation 5 Pro", "konsole", [], [], 1400, alt=["ps5 pro", "playstation 5 pro"]),
    PackItem("Xbox Series X", "konsole", ["xbox series x"], [], 700),
    PackItem("Xbox Series S", "konsole", ["xbox series s"], [], 350),
    # --- graphics cards
    PackItem("RTX 3060", G, ["rtx 3060"], ["3060 ti"], 350),
    PackItem("RTX 3060 Ti", G, ["rtx 3060 ti"], [], 450),
    PackItem("RTX 3070", G, ["rtx 3070"], ["3070 ti"], 550),
    PackItem("RTX 3080", G, ["rtx 3080"], ["3080 ti"], 900),
    PackItem("RTX 3090", G, ["rtx 3090"], ["3090 ti"], 1400),
    PackItem("RTX 4060", G, ["rtx 4060"], ["4060 ti"], 500),
    PackItem("RTX 4060 Ti", G, ["rtx 4060 ti"], [], 700),
    PackItem("RTX 4070", G, ["rtx 4070"], ["4070 ti", "4070 super"], 1000),
    PackItem("RTX 4070 Super", G, ["rtx 4070 super"], ["ti super"], 1200),
    PackItem("RTX 4070 Ti", G, ["rtx 4070 ti"], [], 1400),
    PackItem("RTX 4080", G, ["rtx 4080"], [], 2200),
    PackItem("RTX 4090", G, ["rtx 4090"], [], 3500),
    PackItem("RTX 5060 Ti", G, ["rtx 5060 ti"], [], 900),
    PackItem("RTX 5070", G, ["rtx 5070"], ["5070 ti"], 1100),
    PackItem("RTX 5070 Ti", G, ["rtx 5070 ti"], [], 1700),
    PackItem("RX 7800 XT", G, ["rx 7800 xt"], [], 900),
    # --- laptops
    PackItem("MacBook Air M1", "laptopy", ["macbook air", "m1"], [], 1000),
    PackItem("MacBook Air M2", "laptopy", ["macbook air", "m2"], [], 1500),
    PackItem("MacBook Air M3", "laptopy", ["macbook air", "m3"], [], 2200),
    PackItem("MacBook Pro M1", "laptopy", ["macbook pro", "m1"], [], 1500),
    PackItem("MacBook Pro M2", "laptopy", ["macbook pro", "m2"], [], 2200),
    PackItem("MacBook Pro M3", "laptopy", ["macbook pro", "m3"], [], 3000),
    # --- desktop PCs (compared with PCs that have the same graphics card)
    PackItem("PC z RTX 3060", "komputery", ["rtx 3060"], ["3060 ti"], 1200),
    PackItem("PC z RTX 3070", "komputery", ["rtx 3070"], ["3070 ti"], 1600),
    PackItem("PC z RTX 4060", "komputery", ["rtx 4060"], ["4060 ti"], 1500),
    PackItem("PC z RTX 4070", "komputery", ["rtx 4070"], [], 2200),
    PackItem("PC z RTX 5060", "komputery", ["rtx 5060"], ["5060 ti"], 1800),
    PackItem("PC z RTX 5060 Ti", "komputery", ["rtx 5060 ti"], [], 2200),
    PackItem("PC z RTX 5070", "komputery", ["rtx 5070"], ["5070 ti"], 2600),
    PackItem("PC z RTX 5070 Ti", "komputery", ["rtx 5070 ti"], [], 3200),
]


def draft_kwargs(item: PackItem) -> dict:
    """Arguments for SearchDraft (kept here so the domain stays free of service imports)."""
    return dict(name=item.name, sources=["olx"], keywords=list(item.keywords), alt_keywords=list(item.alt),
                excluded_keywords=list(item.excluded), category=item.category,
                price_min=Decimal(item.price_min) if item.price_min else None, condition="used",
                deal_min_discount=DEAL_PERCENT, interval_seconds=INTERVAL)
