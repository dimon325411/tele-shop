"""Product variant detection for resellers.

Prices are comparable only within the same configuration: an iPhone 15 Pro 128 GB and 512 GB are
different markets. ``variant_of`` extracts the storage size from the title; ``is_junk`` flags listings
that are not a working device (damaged, parts, locked, accessories) so they never pollute the median
or show up as a "deal".
"""

from __future__ import annotations

import re

from app.domain.text import normalize

_STORAGE = re.compile(r"\b(32|64|128|256|512)\s*gb\b|\b(1|2)\s*tb\b")

# anywhere in the title: damaged / locked / not a sale of a device
JUNK_WORDS = [
    "uszkodzony", "uszkodzona", "uszkodzone", "pekniety", "peknieta", "pekniete", "zbity", "zbita",
    "nie dziala", "niedzialajacy", "na czesci", "do naprawy", "zalany", "zalana", "bootloop", "wadliwy",
    "icloud", "blokada", "zablokowany", "zablokowana", "simlock",
    "zamienie", "zamiana", "kupie", "skupuje", "skup", "atrapa", "makieta",
    "zarezerwowane", "zarezerwowany", "zarezerwowana", "rezerwacja", "sprzedane", "sprzedany", "sprzedana",
]
# several different models in one title ("iPhone 15 Pro Max iPhone 16 Pro ...") = a shop list, not one device
_MODEL_NUMBER = re.compile(r"\b(?:iphone|rtx|gtx|rx)\s*(\d{2,4})")
# only when the title STARTS with them ("Etui do iPhone 15 Pro"), since good offers often
# list extras ("iPhone 15 Pro + etui + pudelko")
ACCESSORY_PREFIXES = ["etui", "case", "szklo", "szybka", "folia", "obudowa", "ladowarka", "kabel", "pudelko",
                      "plecki", "wyswietlacz", "bateria", "pasek", "uchwyt", "korpus", "klapka", "czesci"]
_JUNK = [w.split() for w in JUNK_WORDS]


def variant_of(title: str | None) -> str | None:
    """'iPhone 15 Pro 256GB tytan' -> '256gb'; None when the title has no storage size."""
    m = _STORAGE.search(normalize(title)) if title else None
    if not m:
        return None
    return f"{m.group(1)}gb" if m.group(1) else f"{m.group(2)}tb"


def is_junk(title: str | None) -> bool:
    """True for damaged/locked devices, parts, accessories, swap or "will buy" posts (title only)."""
    tokens = normalize(title).split() if title else []
    if not tokens:
        return False
    if tokens[0] in ACCESSORY_PREFIXES or (tokens[0] in {"nowe", "nowy", "nowa", "oryginalne", "oryginalny"}
                                           and len(tokens) > 1 and tokens[1] in ACCESSORY_PREFIXES):
        return True
    for words in _JUNK:
        n = len(words)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == words:
                return True
    return len(set(_MODEL_NUMBER.findall(" ".join(tokens)))) > 1
