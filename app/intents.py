"""Free-text questions -> bot actions, without an LLM (works in Polish and Russian).

"покажи график изменения цены на айфон 15 про"   -> ("chart", <search 'iPhone 15 Pro'>, None)
"покажи айфон на котором заработаем, спред 15%"  -> ("deals", <search>, 15)
The search is picked by word overlap with the user's own search names/keywords
(with simple RU->PL transliteration of common product words).
"""

from __future__ import annotations

import re

from app.domain.models import Search
from app.domain.text import tokens

STATS_WORDS = {"statystyki", "statystyka", "mediana", "srednia", "stats", "статистика", "статистику", "медиана",
               "медиану", "средняя", "среднюю", "цены", "цена", "почем", "почём", "рынок", "ceny", "cena"}
CHART_WORDS = {"wykres", "historia", "chart", "график", "графика", "динамика", "история", "изменения"}
DEALS_WORDS = {"okazja", "okazje", "okazji", "zarobic", "zarobimy", "zysk", "deal", "deals", "tanio", "spread",
               "заработ", "заработаем", "заработать", "выгод", "выгодно", "выгодный", "профит", "спред", "дешевле"}
RU_PRODUCT = {
    "айфон": "iphone", "айфона": "iphone", "айфоны": "iphone", "про": "pro", "макс": "max", "мини": "mini",
    "плейстейшн": "playstation", "плейстейшен": "playstation", "пс5": "ps5", "иксбокс": "xbox", "хбокс": "xbox",
    "ноутбук": "laptop", "ноутбуки": "laptop", "макбук": "macbook", "видеокарта": "karta", "видеокарты": "karta",
    "компьютер": "komputer", "компьютеры": "komputer", "приставка": "konsola", "приставки": "konsola",
}
_PCT = re.compile(r"(\d{1,2})\s*%")


def _words(text: str) -> list[str]:
    raw = re.findall(r"\w+", text.lower())
    return [RU_PRODUCT.get(w, w) for w in raw]


def _has(words: list[str], vocab: set[str]) -> bool:
    return any(w in vocab or any(w.startswith(v) for v in vocab if len(v) >= 6) for w in words)


def detect(text: str, searches: list[Search]) -> tuple[str | None, list[Search], int | None]:
    """Return (intent, candidate searches with the highest word overlap, spread %)."""
    words = _words(_PCT.sub(" ", text))  # "15%" is a spread, not a model number
    intent = ("deals" if _has(words, DEALS_WORDS) else "chart" if _has(words, CHART_WORDS)
              else "stats" if _has(words, STATS_WORDS) else None)
    if intent is None:
        return None, [], None
    pct_match = _PCT.search(text)
    pct = int(pct_match.group(1)) if pct_match else None
    query = set(tokens(" ".join(words)))
    scored: list[tuple[int, Search]] = []
    for s in searches:
        target = set(tokens(" ".join([s.name, *s.criteria.keywords, *s.criteria.alt_keywords])))
        overlap = len(query & target)
        if overlap:
            scored.append((overlap, s))
    if not scored:
        return intent, [], pct
    best = max(o for o, _ in scored)
    return intent, [s for o, s in scored if o == best], pct
