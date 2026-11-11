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
from app.domain.pricing import PriceStats, price_stats, remove_outliers
from app.domain.timing import Clock, utcnow
from app.domain.variants import has_description_defect, is_junk, listing_variant, needs_configuration, product_model
from app.storage.repo import Repository

ACTIVE_WINDOW = timedelta(days=3)  # a listing seen within this window is considered still on the market
MARKET_LIMIT = 2000
ALL_VARIANTS = "all"
FRESH_WINDOW = timedelta(hours=24)  # only listings put up within this window are shown with links


def is_fresh(listing: StoredListing, now: datetime) -> bool:
    """Put up (or, without a date, first seen) within the last 24 h. Older listings feed statistics only."""
    posted = listing.posted_at or listing.first_seen_at
    return posted is not None and posted >= now - FRESH_WINDOW


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
    return (listing.price is not None and listing.price > 0 and not is_junk(listing.title)
            and not has_description_defect(listing.description))


def stats_for(listing: StoredListing, market: list[StoredListing]) -> PriceStats | None:
    """Median of comparable listings: same variant (if known) and same condition (if enough data)."""
    variant = listing_variant(listing)
    if not listing.currency or listing.condition not in {'new', 'used'}:
        return None
    if variant is None and needs_configuration(listing):
        return None
    pool = [m for m in market if m.id != listing.id and usable(m) and m.currency == listing.currency]
    model = product_model(listing.title)
    if model:
        pool = [m for m in pool if product_model(m.title) == model]
    if variant is not None:
        # never mix variants: 128 GB vs 512 GB are different markets (too few -> no verdict)
        pool = [m for m in pool if listing_variant(m) == variant]
    if listing.condition:
        same_condition = [m for m in pool if m.condition == listing.condition]
        stats = price_stats([m.price for m in same_condition], same_condition=True)
        if stats is not None:
            return replace(stats, variant=variant)
        return None  # never replace a small used sample with new or unknown-condition devices
    stats = price_stats([m.price for m in pool])
    return replace(stats, variant=variant) if stats else None


class MarketService:
    def __init__(self, repo: Repository, clock: Clock = utcnow) -> None:
        self.repo = repo
        self.clock = clock

    async def market(self, search_id: int) -> list[StoredListing]:
        """Active listings of the search that match its CURRENT criteria (filters may have been tightened)."""
        from app.domain.matcher import match

        listings = await self.repo.list_active_matches(search_id, self.clock() - ACTIVE_WINDOW, MARKET_LIMIT)
        search = await self.repo.get_search(search_id)
        if search is None:
            return listings
        condition = getattr(search.criteria.condition, 'value', search.criteria.condition)
        return [l for l in listings if match(l, search.criteria).matched
                and l.currency == search.criteria.currency and l.condition in {'new', 'used'}
                and (condition == 'any' or l.condition == condition)]

    async def groups(self, search_id: int) -> dict[str, list[StoredListing]]:
        market = [m for m in await self.market(search_id) if usable(m)]
        split_condition = len({m.condition for m in market}) > 1
        split_model = len({product_model(m.title) for m in market}) > 1
        groups: dict[str, list[StoredListing]] = {}
        for listing in market:
            key = listing_variant(listing) or ('unknown' if needs_configuration(listing) else ALL_VARIANTS)
            if split_model and product_model(listing.title):
                key = product_model(listing.title) + '__' + key
            if split_condition:
                key += '__' + listing.condition
            groups.setdefault(key, []).append(listing)
        return groups

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
        if listing.price * 100 > stats.median * (100 - min_discount):
            return None
        return Deal(listing, stats, -diff, listing_variant(listing))

    async def find_deals(self, search: Search, min_discount: int, limit: int = 5) -> list[Deal]:
        now = self.clock()
        market = await self.market(search.id)
        deals = []
        for listing in market:
            if not is_fresh(listing, now) or not usable(listing):
                continue
            stats = stats_for(listing, market)
            if stats is None:
                continue
            diff = stats.diff_percent(listing.price)
            if listing.price * 100 <= stats.median * (100 - min_discount):
                deals.append(Deal(listing, stats, -diff, listing_variant(listing)))
        deals.sort(key=lambda d: (-d.discount, -(d.listing.posted_at or now).timestamp()))
        return deals[:limit]

    async def take_snapshot(self, search_id: int, day: date | None = None) -> int:
        """Store today's market stats per variant (and overall). Returns number of rows written."""
        day = day or self.clock().date()
        groups = await self.groups(search_id)
        overall = self._group_items(groups, ALL_VARIANTS)
        if overall:
            groups = {**groups, ALL_VARIANTS: overall}
        written = 0
        for variant, items in groups.items():
            if 'unknown' in variant:
                continue
            prices = [m.price for m in items]
            stats = price_stats(prices)
            if stats is None:
                continue
            key = self._snapshot_key(variant, items)
            await self.repo.upsert_snapshot(search_id, day, key, stats.count, stats.median, stats.p25,
                                            stats.p75, min(remove_outliers(prices)))
            written += 1
        return written

    @staticmethod
    def _group_items(groups, variant):
        if variant != ALL_VARIANTS:
            return groups.get(variant, [])
        items = [m for group in groups.values() for m in group]
        if len({m.condition for m in items}) > 1 or len({product_model(m.title) for m in items}) > 1:
            return []
        return items

    @staticmethod
    def _snapshot_key(variant, items):
        first = items[0]
        model = product_model(first.title) or 'generic'
        return f'v3_{first.currency}_{first.condition}_{model}_{variant}'

    async def history(self, search_id: int, variant: str = ALL_VARIANTS, days: int = 183) -> list[dict]:
        since = (self.clock() - timedelta(days=days)).date()
        items = self._group_items(await self.groups(search_id), variant)
        if not items or 'unknown' in variant:
            return []
        return await self.repo.list_snapshots(search_id, self._snapshot_key(variant, items), since)

    async def variants(self, search_id: int) -> list[tuple[str, int]]:
        groups = await self.groups(search_id)
        return sorted(((v, len(items)) for v, items in groups.items()
                       if v != ALL_VARIANTS and 'unknown' not in v), key=lambda kv: -kv[1])

    async def current_prices(self, search_id: int, variant: str = ALL_VARIANTS) -> list[Decimal]:
        items = self._group_items(await self.groups(search_id), variant)
        return remove_outliers([m.price for m in items]) if 'unknown' not in variant else []
