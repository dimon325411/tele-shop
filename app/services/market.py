"""Market analytics for resellers: variant-aware medians, deal finder, price history snapshots.

The "market" of a search = listings matched by that search that are still active (seen recently),
excluding junk (damaged, locked, accessories, swaps) and compared within the same storage variant
(and the same condition when there is enough data). No extra requests are made to the source:
everything is computed from listings the monitoring already collects.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.domain.models import Search, StoredListing
from app.domain.pricing import MIN_SAMPLE, PriceStats, price_stats
from app.domain.timing import Clock, utcnow
from app.domain.variants import is_junk, variant_of
from app.storage.repo import Repository

ACTIVE_WINDOW = timedelta(days=3)  # a listing seen within this window is considered still on the market
MARKET_LIMIT = 2000
ALL_VARIANTS = "all"


@dataclass
class Deal:
    listing: StoredListing
    stats: PriceStats
    discount: int  # percent below the median (positive number)
    variant: str | None = None

    @property
    def potential_profit(self) -> Decimal:
        return (self.stats.median - self.listing.price).quantize(Decimal("1"))


def usable(listing: StoredListing) -> bool:
    return listing.price is not None and listing.price > 0 and not is_junk(listing.title)


def stats_for(listing: StoredListing, market: list[StoredListing]) -> PriceStats | None:
    """Median of comparable listings: same variant (if known) and same condition (if enough data)."""
    variant = variant_of(listing.title)
    pool = [m for m in market if m.id != listing.id and usable(m) and m.currency == listing.currency]
    if variant is not None:
        # never mix variants: 128 GB vs 512 GB are different markets (too few -> no verdict)
        pool = [m for m in pool if variant_of(m.title) == variant]
    if listing.condition:
        same_condition = [m for m in pool if m.condition == listing.condition]
        stats = price_stats([m.price for m in same_condition], same_condition=True)
        if stats is not None:
            return replace(stats, variant=variant)
    stats = price_stats([m.price for m in pool])
    return replace(stats, variant=variant) if stats else None


class MarketService:
    def __init__(self, repo: Repository, clock: Clock = utcnow) -> None:
        self.repo = repo
        self.clock = clock

    async def market(self, search_id: int) -> list[StoredListing]:
        return await self.repo.list_active_matches(search_id, self.clock() - ACTIVE_WINDOW, MARKET_LIMIT)

    async def stats_for_listing(self, search_id: int, listing: StoredListing) -> PriceStats | None:
        if listing.price is None:
            return None
        return stats_for(listing, await self.market(search_id))

    async def evaluate_deal(self, search_id: int, listing: StoredListing, min_discount: int) -> Deal | None:
        """A listing is a deal when it is usable and at least ``min_discount`` % below its market median."""
        if not usable(listing):
            return None
        stats = await self.stats_for_listing(search_id, listing)
        if stats is None:
            return None
        diff = stats.diff_percent(listing.price)
        if diff > -min_discount:
            return None
        return Deal(listing, stats, -diff, variant_of(listing.title))

    async def find_deals(self, search: Search, min_discount: int, fresh_days: int = 7, limit: int = 5) -> list[Deal]:
        now = self.clock()
        market = await self.market(search.id)
        deals = []
        for listing in market:
            posted = listing.posted_at or listing.first_seen_at
            if posted is None or posted < now - timedelta(days=fresh_days) or not usable(listing):
                continue
            stats = stats_for(listing, market)
            if stats is None:
                continue
            diff = stats.diff_percent(listing.price)
            if diff <= -min_discount:
                deals.append(Deal(listing, stats, -diff, variant_of(listing.title)))
        deals.sort(key=lambda d: (-d.discount, -(d.listing.posted_at or now).timestamp()))
        return deals[:limit]

    async def take_snapshot(self, search_id: int, day: date | None = None) -> int:
        """Store today's market stats per variant (and overall). Returns number of rows written."""
        market = [m for m in await self.market(search_id) if usable(m)]
        day = day or self.clock().date()
        groups: dict[str, list[Decimal]] = {ALL_VARIANTS: [m.price for m in market]}
        for m in market:
            v = variant_of(m.title)
            if v:
                groups.setdefault(v, []).append(m.price)
        written = 0
        for variant, prices in groups.items():
            stats = price_stats(prices)
            if stats is None:
                continue
            await self.repo.upsert_snapshot(search_id, day, variant, stats.count, stats.median, stats.p25, stats.p75,
                                            min(prices))
            written += 1
        return written

    async def history(self, search_id: int, variant: str = ALL_VARIANTS, days: int = 183) -> list[dict]:
        since = (self.clock() - timedelta(days=days)).date()
        return await self.repo.list_snapshots(search_id, variant, since)

    async def variants(self, search_id: int) -> list[tuple[str, int]]:
        """Variants present on the current market with their listing counts (most common first)."""
        counts: dict[str, int] = {}
        for m in await self.market(search_id):
            if usable(m):
                v = variant_of(m.title)
                if v:
                    counts[v] = counts.get(v, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    async def current_prices(self, search_id: int, variant: str = ALL_VARIANTS) -> list[Decimal]:
        return [m.price for m in await self.market(search_id)
                if usable(m) and (variant == ALL_VARIANTS or variant_of(m.title) == variant)]
