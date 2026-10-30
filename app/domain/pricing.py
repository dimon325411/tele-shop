"""Price comparison against similar listings (listings matched by the same search).

Median instead of mean (robust to "9999 zł" placeholders), outliers removed with Tukey fences
(1.5 × IQR), and no verdict below ``MIN_SAMPLE`` comparable prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import median, quantiles

MIN_SAMPLE = 5


@dataclass(frozen=True)
class PriceStats:
    median: Decimal
    p25: Decimal
    p75: Decimal
    count: int
    same_condition: bool = False

    def diff_percent(self, price: Decimal) -> int:
        """Signed difference of ``price`` vs the median, in whole percent (negative = cheaper)."""
        if self.median <= 0:
            return 0
        pct = (price - self.median) / self.median * 100
        return int(pct.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def remove_outliers(prices: list[Decimal]) -> list[Decimal]:
    if len(prices) < 4:
        return list(prices)
    q1, _, q3 = quantiles(prices, n=4, method="inclusive")
    iqr = q3 - q1
    low, high = q1 - Decimal("1.5") * iqr, q3 + Decimal("1.5") * iqr
    return [p for p in prices if low <= p <= high]


def price_stats(prices: list[Decimal], same_condition: bool = False) -> PriceStats | None:
    clean = remove_outliers([p for p in prices if p is not None and p > 0])
    if len(clean) < MIN_SAMPLE:
        return None
    q1, _, q3 = quantiles(clean, n=4, method="inclusive")
    return PriceStats(median=Decimal(median(clean)), p25=Decimal(q1), p75=Decimal(q3), count=len(clean),
                      same_condition=same_condition)


def verdict_key(diff_percent: int) -> str:
    """i18n key describing the difference."""
    if diff_percent <= -5:
        return "price.cheaper"
    if diff_percent >= 5:
        return "price.pricier"
    return "price.typical"
