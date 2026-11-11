""""Oddam za darmo" scanner (personal mode, OLX).

Every run (about every 15 minutes): 2 requests for the newest "oddam" / "za darmo" offers, then for at
most ``MAX_PRICE_CHECKS`` fresh free offers in the owner's area one request each to price the same
thing among PAID offers. Owners with the option on get a message when the median resale value is at
least ``FREE_MIN_VALUE`` zł. Examination markers use persistent job state.
Eligible offers are queued in the persistent notification outbox; delivery applies access checks,
quiet hours and retry backoff.
All requests go through the same polite fetcher as monitoring (>=10 s apart, pause on 403/429).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from app.domain.area import in_area
from app.domain.freebies import (FREE_MIN_VALUE, MIN_COMPARABLES, comparable, is_free_junk, is_free_offer,
                                 value_query)
from app.domain.models import Listing, SearchCriteria, User
from app.domain.pricing import PriceStats, price_stats
from app.domain.timing import Clock, utcnow
from app.domain.variants import is_junk
from app.sources.base import SourceError

log = logging.getLogger(__name__)

FREE_QUERIES = ["oddam", "za darmo"]
FREE_MAX_AGE = timedelta(hours=6)  # a free item older than this is usually gone
MAX_PRICE_CHECKS = 4
SEEN_TTL = timedelta(days=3)
PRICE_CACHE_TTL = timedelta(hours=12)


@dataclass
class FreebieOutcome:
    scanned: int = 0
    free: int = 0
    in_area: int = 0
    priced: int = 0
    queued: int = 0
    too_cheap: int = 0
    no_estimate: int = 0
    skipped_reasons: dict = field(default_factory=dict)


class FreebieService:
    def __init__(self, repo, registry, clock: Clock = utcnow, min_value: int = FREE_MIN_VALUE) -> None:
        self.repo, self.registry, self.clock, self.min_value = repo, registry, clock, min_value
        self._price_cache: dict[str, tuple] = {}

    def _source(self):
        from app.sources.olx import OlxPersonalSource

        source = self.registry.get("olx")
        if not self.registry.is_enabled("olx") or not isinstance(source, OlxPersonalSource):
            return None
        return source

    async def _get(self, source, query: str) -> dict:
        from app.sources.olx import build_url

        text = await source.fetcher.get(build_url(query, SearchCriteria(keywords=[query])))
        try:
            return json.loads(text)
        except ValueError:
            return {}

    async def scan(self, users: list[User], notifier=None) -> FreebieOutcome:
        out = FreebieOutcome()
        owners = [u for u in users if getattr(u, "free_alerts", False) and getattr(u, "deal_city", None)
                  and not u.bot_blocked and not u.is_suspended and not u.deleted_at]
        source = self._source()
        if not owners or source is None:
            return out
        now = self.clock()
        candidates: list[Listing] = []
        try:
            for query in FREE_QUERIES:
                data = await self._get(source, query)
                for item in data.get("data") or []:
                    out.scanned += 1
                    if not isinstance(item, dict) or not is_free_offer(item):
                        continue
                    out.free += 1
                    listing = self._parse(item, source)
                    if listing is None:
                        continue
                    state = await self.repo.get_job_state(f"free:{listing.external_id}")
                    if state and state.get("until", "") > now.isoformat():
                        continue
                    posted = listing.posted_at
                    if posted is None or now - posted > FREE_MAX_AGE or is_free_junk(listing.title):
                        continue
                    if not any(in_area(u, listing) for u in owners):
                        continue
                    if listing.external_id not in {c.external_id for c in candidates}:
                        candidates.append(listing)
            out.in_area = len(candidates)
            for listing in candidates[:MAX_PRICE_CHECKS]:
                stats = await self.estimate(source, listing.title)
                if stats is None:
                    out.no_estimate += 1
                    await self.repo.save_job_state(f"free:{listing.external_id}",
                                                   {"until": (now + timedelta(minutes=30)).isoformat()}, now)
                    continue
                out.priced += 1
                if stats.median < self.min_value:
                    out.too_cheap += 1
                    await self.repo.save_job_state(f"free:{listing.external_id}",
                                                   {"until": (now + SEEN_TTL).isoformat()}, now)
                    continue
                stored = (await self.repo.upsert_listing(listing, now)).listing
                payload = {'median': str(stats.median), 'p25': str(stats.p25), 'p75': str(stats.p75),
                           'count': stats.count}
                async with self.repo.transaction():
                    for user in owners:
                        if in_area(user, listing):
                            out.queued += int(await self.repo.enqueue_notification(
                                user.id, stored.id, None, 'free', f'free:{stored.id}', stored.price, now,
                                payload=payload))
                    await self.repo.save_job_state(f'free:{listing.external_id}',
                                                   {'until': (now + SEEN_TTL).isoformat()}, now)
        except SourceError as exc:  # blocked / unavailable: monitoring handles the pause, we just stop
            log.warning("free scan stopped: %s", exc)
        log.info("free scan: %s", out)
        return out

    async def estimate(self, source, title: str) -> PriceStats | None:
        """Median price of PAID offers with the same key words (None: too few to tell)."""
        query = value_query(title)
        if query is None:
            return None
        now = self.clock()
        cached = self._price_cache.get(query)
        if cached and now - cached[0] < PRICE_CACHE_TTL:
            return cached[1]
        data = await self._get(source, query)
        prices: list[Decimal] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict) or is_free_offer(item):
                continue
            listing = self._parse(item, source)
            if listing and listing.price and comparable(listing.title, query) and not is_junk(listing.title):
                prices.append(listing.price)
        stats = price_stats(prices) if len(prices) >= MIN_COMPARABLES else None
        if stats is not None:
            self._price_cache[query] = (now, stats)
        return stats

    @staticmethod
    def _parse(item: dict, source) -> Listing | None:
        from app.sources.olx import parse_offers

        try:
            parsed = parse_offers({"data": [item]}, source.categories)
        except Exception:
            return None
        return parsed[0] if parsed else None
