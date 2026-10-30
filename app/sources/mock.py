"""Mock source with clearly fake listings for development and tests.

All listings have ``is_test=True``, a "[TEST]" title prefix and URLs on the reserved
``example.invalid`` domain, so they can never be mistaken for real offers.

The catalogue grows over time: every call to ``fetch`` may "publish" one more listing,
which lets the full pipeline (new listing -> match -> notification) be exercised locally.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from decimal import Decimal

from app.domain.models import Listing, SearchCriteria
from app.sources.base import SourceAdapter, SourceCapabilities, SourceUnavailable

TEST_DOMAIN = "https://example.invalid/test-listing/"

_SEED: list[tuple[str, str, str | None, str | None, bool | None, str]] = [
    # title, price, city, condition, delivery, category
    ("Rower górski Kross Hexagon 29", "1200", "Kraków", "used", True, "sport"),
    ("iPhone 13 128GB czarny", "1899.99", "Warszawa", "used", True, "elektronika"),
    ("iPhone 14 Pro uszkodzony ekran", "1500", "Gdańsk", "used", False, "elektronika"),
    ("Sofa narożna szara", "650", "Wrocław", "used", False, "dom"),
    ("Konsola PlayStation 5 nowa", "2299", "Poznań", "new", True, "elektronika"),
    ("Rowerek biegowy dla dziecka", None, "Kraków", None, None, "dzieci"),
    ("Laptop Lenovo ThinkPad T14", "2100", "Łódź", "used", True, "elektronika"),
    ("Kurtka zimowa męska XL", "120", "Lublin", "used", True, "moda"),
]

_GROWTH = [
    ("Rower szosowy Giant Contend", "2400", "Kraków", "used", True, "sport"),
    ("iPhone 13 mini 256GB", "1650", "Warszawa", "used", True, "elektronika"),
    ("Rower miejski damski", "450", "Katowice", "used", False, "sport"),
    ("Monitor Dell 27 cali", "700", "Warszawa", "used", True, "elektronika"),
]


def _make(source: str, idx: int, row: tuple) -> Listing:
    title, price, city, condition, delivery, category = row
    return Listing(
        source=source,
        external_id=f"test-{idx}",
        url=f"{TEST_DOMAIN}{idx}",
        title=f"[TEST] {title}",
        price=Decimal(price) if price is not None else None,
        currency="PLN",
        city=city,
        condition=condition,
        delivery_available=delivery,
        category=category,
        is_test=True,
    )


class MockSource(SourceAdapter):
    code = "mock"
    display_name = "Źródło testowe"
    is_test_source = True

    def __init__(self, grow: bool = True, fail_times: int = 0) -> None:
        self._listings = [_make(self.code, i, row) for i, row in enumerate(_SEED, start=1)]
        self._growth = itertools.cycle(_GROWTH) if grow else None
        self._next_idx = len(_SEED) + 1
        self._fail_times = fail_times
        self.calls = 0

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(keywords=False, notes=["Dane testowe, nie są prawdziwymi ogłoszeniami."])

    def publish(self, listing: Listing) -> None:
        """Test helper: add a listing to the catalogue."""
        self._listings.append(replace(listing, source=self.code, is_test=True))

    def set_price(self, external_id: str, price: Decimal) -> None:
        self._listings = [replace(l, price=price) if l.external_id == external_id else l for l in self._listings]

    async def fetch(self, criteria: SearchCriteria, limit: int = 50) -> list[Listing]:
        self.calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise SourceUnavailable("mock temporary failure")
        if self._growth is not None and self.calls > 1:
            self._listings.append(_make(self.code, self._next_idx, next(self._growth)))
            self._next_idx += 1
        newest_first = list(reversed(self._listings))
        return newest_first[:limit]
