"""OLX.pl adapter for PERSONAL MODE only (``PERSONAL_MODE=true``).

Why only personal: OLX's terms (pt. 2.3) forbid aggregating service data to pass it on to third
parties, so the commercial bot keeps OLX disabled (``app/sources/disabled.py``). In personal mode
the bot serves only its owner, at a low request rate — see docs/PERSONAL_MODE.md for the risks.

Access method: the public offers endpoint ``/api/v1/offers/`` that OLX's robots.txt explicitly
allows for crawlers (``Allow: /api/v1/offers/``). No login, no cookies, no CAPTCHA solving,
honest User-Agent. Any 403/429/CAPTCHA stops collection (``SourceBlocked``) — never bypassed.

The JSON structure was written from public knowledge of the endpoint and could NOT be verified
from the development environment (OLX was unreachable). Run ``python scripts/olx_probe.py rower``
once on your computer: it makes one request and reports whether the parser understands it.
Unknown structure => ``SourceLayoutChanged`` (the search pauses, admin sees it in /admin_sources).
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlparse

from app.domain.models import Listing, SearchCriteria
from app.sources.base import SourceAdapter, SourceCapabilities, SourceLayoutChanged
from app.sources.http import PoliteFetcher
from app.sources.olx_categories import PRESETS, OlxCategories

BASE = "https://www.olx.pl/api/v1/offers/"
MAX_QUERIES_PER_CHECK = 3
PAGE_LIMIT = 40

_CONDITION = {"used": "used", "uzywane": "used", "new": "new", "nowe": "new"}


def build_queries(criteria: SearchCriteria) -> list[str]:
    """OLX full-text query strings for a search (our matcher filters precisely afterwards)."""
    if criteria.keywords:
        return [" ".join(criteria.keywords)]
    return list(criteria.alt_keywords[:MAX_QUERIES_PER_CHECK])


def build_url(query: str, criteria: SearchCriteria, limit: int = PAGE_LIMIT, category_id: int | None = None) -> str:
    params: dict[str, Any] = {"offset": 0, "limit": limit, "query": query, "sort_by": "created_at:desc"}
    if category_id is not None:
        params["category_id"] = category_id
    if criteria.price_min is not None:
        params["filter_float_price:from"] = int(criteria.price_min)
    if criteria.price_max is not None:
        params["filter_float_price:to"] = int(criteria.price_max) + 1  # our matcher applies the exact bound
    return f"{BASE}?{urlencode(params)}"


def _price(params: list) -> tuple[Decimal | None, str | None]:
    for p in params or []:
        if isinstance(p, dict) and p.get("key") == "price":
            value = p.get("value") or {}
            if not isinstance(value, dict):
                return None, None
            raw = value.get("value")
            try:
                price = Decimal(str(raw)) if raw is not None else None
            except InvalidOperation:
                price = None
            if price is not None and price <= 0:  # "Za darmo" / "Zamienię" style values
                price = None
            return price, (value.get("currency") or "PLN")
    return None, None


def _condition(params: list) -> str | None:
    for p in params or []:
        if isinstance(p, dict) and p.get("key") == "state":
            value = p.get("value") or {}
            key = str(value.get("key") or value.get("label") or "").lower() if isinstance(value, dict) else ""
            return _CONDITION.get(key)
    return None


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _name(obj: Any) -> str | None:
    return obj.get("name") if isinstance(obj, dict) and obj.get("name") else None


PHOTO_SIZE = (1000, 750)


def _photo(item: dict) -> str | None:
    photos = item.get("photos")
    if not isinstance(photos, list) or not photos or not isinstance(photos[0], dict):
        return None
    link = photos[0].get("link")
    if not isinstance(link, str) or not link.startswith("https://"):
        return None
    return link.replace("{width}", str(PHOTO_SIZE[0])).replace("{height}", str(PHOTO_SIZE[1]))


def _delivery(item: dict) -> bool | None:
    delivery = item.get("delivery")
    if isinstance(delivery, dict):
        rock = delivery.get("rock")
        if isinstance(rock, dict) and "active" in rock:
            return bool(rock["active"])
    return None


def parse_offers(payload: str | bytes | dict, categories: "OlxCategories | None" = None) -> list[Listing]:
    """Parse the offers JSON. Raises SourceLayoutChanged when the shape is not recognised."""
    try:
        data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    except ValueError as exc:
        raise SourceLayoutChanged("OLX response is not JSON") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SourceLayoutChanged("OLX response has no 'data' list")
    promoted: set[int] = set()
    meta = data.get("metadata") if isinstance(data, dict) else None
    source_meta = meta.get("source") if isinstance(meta, dict) else None
    if isinstance(source_meta, dict) and isinstance(source_meta.get("promoted"), list):
        promoted = {i for i in source_meta["promoted"] if isinstance(i, int)}
    listings: list[Listing] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or index in promoted:
            continue  # paid placements are shown regardless of date; organic results follow refresh time
        offer_id, url, title = item.get("id"), item.get("url"), item.get("title")
        if offer_id is None or not isinstance(url, str) or not isinstance(title, str):
            continue  # never invent missing identifiers or links
        host = urlparse(url).netloc
        if not host.endswith("olx.pl"):
            continue  # e.g. ads redirecting to partner sites (otodom) are skipped
        price, currency = _price(item.get("params"))
        location = item.get("location") or {}
        mapdata = item.get("map") or {}
        listings.append(Listing(
            source="olx",
            external_id=str(offer_id),
            url=url,
            title=title.strip(),
            price=price,
            currency=currency,
            description=(item.get("description") or None) if isinstance(item.get("description"), str) else None,
            city=_name(location.get("city")) if isinstance(location, dict) else None,
            region=_name(location.get("region")) if isinstance(location, dict) else None,
            condition=_condition(item.get("params")),
            category=categories.preset_of((item.get("category") or {}).get("id")) if categories else None,
            delivery_available=_delivery(item),
            latitude=mapdata.get("lat") if isinstance(mapdata, dict) else None,
            longitude=mapdata.get("lon") if isinstance(mapdata, dict) else None,
            posted_at=_dt(item.get("created_time")),
            seller_created_at=_dt((item.get("user") or {}).get("created")) if isinstance(item.get("user"), dict) else None,
            seller_is_business=item.get("business") if isinstance(item.get("business"), bool) else None,
            photo_url=_photo(item),
            is_test=False,
        ))
    if items and not listings and len(promoted) < len(items):
        raise SourceLayoutChanged("OLX items present but none could be parsed")
    return listings


class OlxPersonalSource(SourceAdapter):
    code = "olx"
    display_name = "OLX.pl"
    is_test_source = False

    def __init__(self, fetcher: PoliteFetcher, categories: OlxCategories | None = None) -> None:
        self.fetcher = fetcher
        self.categories = categories if categories is not None else OlxCategories()

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(keywords=True, price=True, sort_newest=True, category=True,
                                  listing_category=True,
                                  notes=["Tryb prywatny: tylko dla właściciela bota."])

    async def fetch(self, criteria: SearchCriteria, limit: int = PAGE_LIMIT) -> list[Listing]:
        seen: dict[str, Listing] = {}
        category_ids: list[int | None] = self.categories.category_ids(criteria.category) or [None]
        for query in build_queries(criteria):
            for category_id in category_ids:
                text = await self.fetcher.get(build_url(query, criteria, min(limit, PAGE_LIMIT), category_id))
                for listing in parse_offers(text, self.categories):
                    seen.setdefault(listing.external_id, listing)
        return list(seen.values())

    @staticmethod
    def presets() -> list[str]:
        return list(PRESETS)
