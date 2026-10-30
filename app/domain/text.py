"""Text normalisation for Polish listings: lowercase, strip diacritics, tokenise."""

from __future__ import annotations

import re
import unicodedata

# Characters that NFKD does not decompose into base letter + accent.
_SPECIAL = str.maketrans({"ł": "l", "Ł": "l", "ß": "ss", "ø": "o", "æ": "ae", "đ": "d"})
_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(text: str | None) -> str:
    """'Rower GÓRSKI, Kross — 29"' -> 'rower gorski kross 29'."""
    if not text:
        return ""
    text = text.translate(_SPECIAL).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_WORD.sub(" ", text).strip()


def tokens(text: str | None) -> list[str]:
    norm = normalize(text)
    return norm.split() if norm else []


def clean_keyword_list(values: list[str] | None) -> list[str]:
    """Normalise user keywords, drop empties and duplicates, keep order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values or []:
        norm = normalize(value)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def split_user_list(raw: str | None) -> list[str]:
    """Split user input like 'iphone 13, iphone 14; 13 pro' into phrases."""
    if not raw:
        return []
    parts = re.split(r"[,;\n]+", raw)
    return [p.strip() for p in parts if p.strip()]
