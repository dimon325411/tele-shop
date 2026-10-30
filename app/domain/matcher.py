"""Rule-based listing matcher (no ML).

Rules, in order (the first failing rule rejects the listing):
1. currency must match (no conversion);
2. excluded phrases must be absent;
3. every main keyword phrase must be present;
4. at least one alternative phrase must be present (if any are given);
5. price within bounds; a listing *without* a price is rejected when bounds are set;
6. category / condition / location / delivery: if the listing does not provide the field,
   it passes only when ``include_unknown`` is true (the reason says "unknown").

Phrase matching works on normalised tokens (lowercase, no diacritics). A phrase matches
when its tokens appear consecutively. Single tokens match exactly, or loosely for Polish
inflection ("rower" ~ "rowery" ~ "rowerem"); tokens containing digits always match exactly
("13" never matches "130"). Optional fuzzy mode tolerates small typos ("ipohne").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.domain.models import Condition, Delivery, Listing, SearchCriteria
from app.domain.text import normalize, tokens

Reason = tuple[str, dict]


@dataclass
class MatchResult:
    matched: bool
    reasons: list[Reason] = field(default_factory=list)
    rejected_by: str | None = None

    def reason_keys(self) -> list[str]:
        return [key for key, _ in self.reasons]


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def token_matches(keyword: str, token: str, fuzzy: bool = False) -> bool:
    if keyword == token:
        return True
    if _has_digit(keyword) or _has_digit(token):
        return False
    # Polish inflection: compare stems (drop up to 2 trailing letters of the keyword).
    if len(keyword) >= 4:
        stem = keyword[:-2] if len(keyword) >= 6 else keyword[:-1] if len(keyword) >= 5 else keyword
        if token.startswith(stem) and abs(len(token) - len(keyword)) <= 4 and len(token) >= len(stem):
            return True
    if fuzzy and len(keyword) >= 5 and len(token) >= 4:
        allowed = 1 if len(keyword) < 9 else 2
        return abs(len(keyword) - len(token)) <= allowed and edit_distance(keyword, token) <= allowed
    return False


def edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (Levenshtein + adjacent transpositions)."""
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[len(b)]


def phrase_in_tokens(phrase: str, text_tokens: list[str], fuzzy: bool = False) -> bool:
    words = normalize(phrase).split()
    if not words:
        return False
    n = len(words)
    for start in range(len(text_tokens) - n + 1):
        if all(token_matches(w, text_tokens[start + i], fuzzy) for i, w in enumerate(words)):
            return True
    return False


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def match(listing: Listing, criteria: SearchCriteria) -> MatchResult:
    reasons: list[Reason] = []

    def reject(rule: str) -> MatchResult:
        return MatchResult(False, reasons, rule)

    text_tokens = tokens(f"{listing.title} {listing.description or ''}")

    # 1. currency
    if listing.currency and criteria.currency and listing.currency.upper() != criteria.currency.upper():
        return reject("currency")

    # 2. excluded
    for phrase in criteria.excluded_keywords:
        if phrase_in_tokens(phrase, text_tokens, fuzzy=False):
            reasons.append(("reason.excluded", {"word": phrase}))
            return reject("excluded")

    # 3. main keywords (all)
    for phrase in criteria.keywords:
        if not phrase_in_tokens(phrase, text_tokens, criteria.fuzzy):
            return reject("keywords")
        reasons.append(("reason.keyword", {"word": phrase}))

    # 4. alternative keywords (any)
    if criteria.alt_keywords:
        hit = next((p for p in criteria.alt_keywords if phrase_in_tokens(p, text_tokens, criteria.fuzzy)), None)
        if hit is None:
            return reject("alt_keywords")
        reasons.append(("reason.alt_keyword", {"word": hit}))

    if not criteria.keywords and not criteria.alt_keywords:
        return reject("no_keywords")

    # 5. price
    if criteria.price_min is not None or criteria.price_max is not None:
        if listing.price is None:
            return reject("price_unknown")
        if criteria.price_min is not None and listing.price < criteria.price_min:
            return reject("price_min")
        if criteria.price_max is not None and listing.price > criteria.price_max:
            return reject("price_max")
        reasons.append(("reason.price", {"price": str(listing.price)}))

    unknown = criteria.include_unknown

    # 6a. category
    if criteria.category:
        if listing.category is None:
            if not unknown:
                return reject("category_unknown")
            reasons.append(("reason.unknown", {"field": "category"}))
        elif normalize(listing.category) != normalize(criteria.category):
            return reject("category")

    # 6b. condition
    if criteria.condition is not Condition.ANY:
        if listing.condition is None:
            if not unknown:
                return reject("condition_unknown")
            reasons.append(("reason.unknown", {"field": "condition"}))
        elif listing.condition != criteria.condition.value:
            return reject("condition")
        else:
            reasons.append(("reason.condition", {"condition": listing.condition}))

    # 6c. location
    location_result = _check_location(listing, criteria, reasons)
    if location_result:
        return reject(location_result)

    # 6d. delivery
    if criteria.delivery is Delivery.SHIPPING:
        if listing.delivery_available is None:
            if not unknown:
                return reject("delivery_unknown")
            reasons.append(("reason.unknown", {"field": "delivery"}))
        elif not listing.delivery_available:
            return reject("delivery")

    return MatchResult(True, reasons)


def _check_location(listing: Listing, criteria: SearchCriteria, reasons: list[Reason]) -> str | None:
    unknown = criteria.include_unknown
    if (
        criteria.radius_km is not None
        and criteria.center_lat is not None
        and criteria.center_lon is not None
        and listing.latitude is not None
        and listing.longitude is not None
    ):
        dist = haversine_km(criteria.center_lat, criteria.center_lon, listing.latitude, listing.longitude)
        if dist > criteria.radius_km:
            return "radius"
        reasons.append(("reason.distance", {"km": round(dist)}))
        return None
    if criteria.city:
        if listing.city is None:
            if not unknown:
                return "city_unknown"
            reasons.append(("reason.unknown", {"field": "city"}))
        elif normalize(listing.city) != normalize(criteria.city):
            return "city"
        else:
            reasons.append(("reason.city", {"city": listing.city}))
    if criteria.region:
        if listing.region is None:
            if not unknown:
                return "region_unknown"
            reasons.append(("reason.unknown", {"field": "region"}))
        elif normalize(listing.region) != normalize(criteria.region):
            return "region"
    return None
