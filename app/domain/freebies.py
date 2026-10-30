""""Oddam za darmo": free items near the owner that can be resold for at least ``FREE_MIN_VALUE`` zł.

* A free offer is recognised from the OLX price param ("Za darmo", value 0, type "free") or a title
  starting with "Oddam". Wanted/"przyjmę" posts, animals, rubble, food and the like are never offers.
* The resale value is estimated from PAID OLX offers with the same key words (one polite extra request
  per candidate): the median of their prices, only with at least ``MIN_COMPARABLES`` offers.
"""

from __future__ import annotations

import re

from app.domain.text import normalize

FREE_MIN_VALUE = 200
MIN_COMPARABLES = 5
MAX_QUERY_WORDS = 3

# words that only describe the act of giving away / the condition; never part of the product query
STOPWORDS = {
    "oddam", "oddaje", "oddamy", "za", "darmo", "free", "gratis", "odbior", "odbiorem", "osobisty", "wlasny",
    "wlasnym", "transportem", "transport", "pilne", "pilnie", "dzis", "dzisiaj", "jutro", "szybko", "sprawny",
    "sprawna", "sprawne", "dzialajacy", "dzialajaca", "dzialajace", "uzywany", "uzywana", "uzywane", "stan",
    "dobry", "dobra", "dobre", "bardzo", "ladny", "ladna", "ladne", "stary", "stara", "stare", "nowy", "nowa", "nowe",
    "duzy", "duza", "duze", "maly", "mala", "male", "w", "z", "i", "do", "na", "od", "po", "dla", "lub", "oraz",
    "krakow", "krakowie", "okolice", "okolicy", "polecam", "zadbany", "zadbana", "kompletny", "kompletna",
    "cena", "zl", "pln", "zabrania", "zabrac", "wyniesienia", "wyniesienie",
}
# not an offer of a resellable thing
FREE_JUNK = [
    "przyjme", "przygarne", "szukam", "potrzebuje", "kupie", "zamienie",
    "kot", "kotka", "kotek", "kotki", "koty", "pies", "psa", "piesek", "szczeniak", "szczeniaki", "krolik", "chomik",
    "swinka", "papuga", "rybki", "kury", "zwierze", "zwierzeta",
    "gruz", "ziemia", "ziemie", "piasek", "kamienie", "obornik", "galezie", "trawa", "jedzenie",
    "zywnosc", "chleb", "leki", "ubrania", "odziez", "smieci", "makulatura", "zlom", "do naprawy", "na czesci",
    "uszkodzony", "uszkodzona", "zepsuty", "zepsuta", "nie dziala",
]
_JUNK = [w.split() for w in FREE_JUNK]


def is_free_offer(item: dict) -> bool:
    """True for OLX offers given away for free (price param 'Za darmo' / 0 / type free, or 'Oddam ...')."""
    for p in item.get("params") or []:
        if isinstance(p, dict) and p.get("key") == "price":
            value = p.get("value")
            if isinstance(value, dict):
                label = normalize(str(value.get("label") or ""))
                kind = str(value.get("type") or "").lower()
                raw = value.get("value")
                if "darmo" in label or kind in {"free", "darmo"} or raw in (0, "0", 0.0):
                    return True
                try:
                    if raw is not None and float(raw) > 0:
                        return False  # "Oddam iPhone za 500 zł" is a sale
                except (TypeError, ValueError):
                    pass
    return normalize(item.get("title") or "").startswith("oddam")


def is_free_junk(title: str | None) -> bool:
    tokens = normalize(title).split() if title else []
    for words in _JUNK:
        n = len(words)
        if any(tokens[i:i + n] == words for i in range(len(tokens) - n + 1)):
            return True
    return False


def value_query(title: str | None) -> str | None:
    """'Oddam pralkę Bosch Serie 4, sprawna' -> 'pralke bosch serie'. None when nothing meaningful is left."""
    words = [w for w in re.findall(r"[a-z0-9]+", normalize(title or "")) if w not in STOPWORDS]
    words = [w for w in words if len(w) >= 3 or any(ch.isdigit() for ch in w)]
    if not words:
        return None
    return " ".join(words[:MAX_QUERY_WORDS])


def comparable(title: str | None, query: str) -> bool:
    """A paid offer is comparable when its title contains every word of the query (prefix match for Polish
    inflection: 'pralke' ~ 'pralka', 'pralki')."""
    tokens = normalize(title or "").split()
    for word in query.split():
        stem = word[:-1] if len(word) >= 5 and not word[-1].isdigit() else word
        if not any(t.startswith(stem) for t in tokens):
            return False
    return True
