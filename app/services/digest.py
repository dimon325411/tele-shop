"""Evening summary: proof that monitoring works even when there were no hot deals.

One message per day (personal mode, at ``DIGEST_HOUR`` local time): how many new matching listings
each search saw today, how many hot deals were sent, the closest near-misses with links, and
whether a source is paused. Computed only from data the monitoring already stored (no requests).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.models import User
from app.domain.timing import Clock, local_day_start_utc, utcnow
from app.i18n import esc, t
from app.presentation import fmt_posted, fmt_price
from app.services.market import MarketService, is_fresh, stats_for, usable
from app.storage.repo import Repository

NEAR_MISS_LIMIT = 5
RECENT_LIMIT = 500
BASELINE_WINDOW = timedelta(minutes=5)
MAX_SEARCH_LINES = 15  # 50 searches would not fit into one Telegram message
MAX_QUIET_NAMES = 12


@dataclass
class NearMiss:
    search_name: str
    title: str
    price: object
    currency: str
    discount: int  # % below the median (negative = above)
    url: str
    posted: str = ""


class DigestService:
    def __init__(self, repo: Repository, market: MarketService, clock: Clock = utcnow) -> None:
        self.repo, self.market, self.clock = repo, market, clock

    async def build(self, user: User, now: datetime | None = None) -> str | None:
        now = now or self.clock()
        lang = user.language
        day_start = local_day_start_utc(now, user.timezone)
        searches = [s for s in await self.repo.list_user_searches(user.id) if s.is_active]
        if not searches:
            return None
        new_total = 0
        quiet: list[str] = []
        near: list[NearMiss] = []
        lines_per_search: list[tuple[int, str]] = []
        for search in searches:
            recent = await self.repo.list_recent_matches(search.id, day_start, RECENT_LIMIT)
            live_since = (search.created_at or day_start) + BASELINE_WINDOW  # first check only records the market
            fresh = [l for l in recent if is_fresh(l, now)
                     and l.first_seen_at is not None and l.first_seen_at > live_since]
            if not fresh:
                quiet.append(search.name)
                continue
            new_total += len(fresh)
            market = await self.market.market(search.id)
            best: NearMiss | None = None
            for listing in fresh:
                if not usable(listing):
                    continue
                stats = stats_for(listing, market)
                if stats is None:
                    continue
                discount = -stats.diff_percent(listing.price)
                if best is None or discount > best.discount:
                    best = NearMiss(search.name, listing.title, listing.price, listing.currency or "PLN", discount,
                                    listing.url, fmt_posted(listing, now, user.timezone, lang))
            if best is not None:
                near.append(best)
            lines_per_search.append((len(fresh), t("digest.search_line", lang, name=esc(search.name), count=len(fresh),
                                                   best=(t("digest.best", lang, pct=_signed(best.discount))
                                                         if best else t("digest.no_median", lang)))))
        sent = await self.repo.count_sent_since(user.id, day_start)
        parts = [t("digest.title", lang, date=now.astimezone(_tz(user.timezone)).strftime("%d.%m")),
                 t("digest.totals", lang, searches=len(searches), new=new_total, sent=sent)]
        for state in await self.repo.list_source_states():
            if state.blocked_until and state.blocked_until > now or state.status != "ok":
                parts.append(t("digest.source_problem", lang, source=esc(state.source)))
        if lines_per_search:
            lines_per_search.sort(key=lambda x: -x[0])
            shown = lines_per_search[:MAX_SEARCH_LINES]
            text = "\n".join(line for _, line in shown)
            if len(lines_per_search) > len(shown):
                text += "\n" + t("digest.more", lang, count=len(lines_per_search) - len(shown))
            parts.append(text)
        near = sorted((n for n in near if n.discount > 0), key=lambda n: -n.discount)[:NEAR_MISS_LIMIT]
        if near:
            parts.append(t("digest.near_title", lang) + "\n" + "\n".join(
                t("digest.near_item", lang, pct=n.discount, name=esc(n.search_name), title=esc(n.title[:60]),
                  price=fmt_price(n.price), currency=esc(n.currency), url=esc(n.url), posted=n.posted)
                for n in near))
        if quiet:
            names = ", ".join(quiet[:MAX_QUIET_NAMES]) + (f" (+{len(quiet) - MAX_QUIET_NAMES})"
                                                          if len(quiet) > MAX_QUIET_NAMES else "")
            parts.append(t("digest.quiet", lang, names=esc(names)))
        parts.append(t("digest.footer", lang))
        return "\n\n".join(parts)


def _signed(discount: int) -> str:
    return f"−{discount}%" if discount > 0 else f"+{-discount}%"


def _tz(name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)
