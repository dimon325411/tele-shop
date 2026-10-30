import asyncio
import re
import unittest
from datetime import timezone
from decimal import Decimal
from pathlib import Path

from app.domain.models import SearchCriteria, StoredListing, User
from app.i18n import format_interval, t
from app.i18n.pl import MESSAGES
from app.presentation import describe_criteria, fmt_price, render_listing_notification
from app.services.notifications import OutgoingListing
from app.sources.base import SourceBlocked, SourceDisabled, SourceUnavailable
from app.sources.disabled import AllegroLokalnieSource, OlxSource
from app.sources.http import HttpResponse, PoliteFetcher
from app.sources.mock import MockSource
from tests.unit.helpers import T0

ROOT = Path(__file__).resolve().parents[2]


class PresentationTests(unittest.TestCase):
    def _item(self, **kw):
        listing = StoredListing(source="mock", external_id="1", url="https://example.invalid/test-listing/1",
                                title="[TEST] Rower <b>Kross</b>", price=Decimal("1200"), city="Kraków",
                                condition="used", is_test=True, id=5, first_seen_at=T0)
        user = User(id=1, telegram_id=10)
        base = dict(user=user, listing=listing, search_id=3, search_name="Rowery", kind="new", old_price=None)
        base.update(kw)
        return OutgoingListing(**base)

    def test_notification_contains_required_fields(self):
        text, buttons = render_listing_notification(self._item(), T0)
        for fragment in ["Rower &lt;b&gt;Kross&lt;/b&gt;", "1 200 PLN", "Źródło testowe", "Kraków", "Używany",
                         "24.10.2026 12:00", "Rowery", "testowe"]:
            self.assertIn(fragment, text)
        self.assertEqual(buttons[0][0].url, "https://example.invalid/test-listing/1")
        self.assertEqual(buttons[1][0].callback, "s:pause:3")
        self.assertEqual(buttons[1][1].callback, "settings:open")

    def test_price_drop_and_unknown_price(self):
        text, _ = render_listing_notification(self._item(kind="price_drop", old_price=Decimal("1500")), T0)
        self.assertIn("<s>1 500 PLN</s>", text)
        item = self._item()
        item.listing.price = None
        self.assertIn("cena nie podana", render_listing_notification(item, T0)[0])

    def test_formatters(self):
        self.assertEqual(fmt_price(Decimal("1899.99")), "1 899,99")
        self.assertEqual(fmt_price(Decimal("5")), "5")
        self.assertEqual(format_interval(600), "10 min")
        self.assertEqual(format_interval(7200), "2 godz.")
        c = SearchCriteria(keywords=["rower"], price_max=Decimal(500), city="Kraków")
        self.assertEqual(describe_criteria(c), "🔑 rower · 💰 0–500 PLN · 📍 Kraków")
        deal = SearchCriteria(keywords=["iphone"], deal_min_discount=15)
        self.assertIn("okazje ≥15%", describe_criteria(deal))

    def test_t_fallbacks(self):
        self.assertEqual(t("missing.key"), "missing.key")
        self.assertIn("{plan}", t("start.welcome_new", name="x"))  # missing params stay visible, no crash

    def test_all_used_i18n_keys_exist(self):
        """Every literal t("...") key used in the code must exist in the Polish table."""
        used: set[str] = set()
        for path in (ROOT / "app").rglob("*.py"):
            used |= set(re.findall(r"""\bt\(\s*["']([a-z_]+\.[a-z0-9_.]+)["']""", path.read_text(encoding="utf-8")))
        missing = sorted(k for k in used if k not in MESSAGES)
        self.assertEqual(missing, [])
        self.assertGreater(len(used), 50)


class SourceTests(unittest.TestCase):
    def test_mock_marks_everything_as_test(self):
        src = MockSource()
        first = asyncio.run(src.fetch(SearchCriteria(keywords=["x"])))
        second = asyncio.run(src.fetch(SearchCriteria(keywords=["x"])))
        self.assertEqual(len(second), len(first) + 1)  # catalogue grows
        for listing in second:
            self.assertTrue(listing.is_test)
            self.assertTrue(listing.title.startswith("[TEST]"))
            self.assertTrue(listing.url.startswith("https://example.invalid/"))

    def test_real_sources_are_disabled(self):
        for src in (OlxSource(), AllegroLokalnieSource()):
            with self.assertRaises(SourceDisabled):
                asyncio.run(src.fetch(SearchCriteria(keywords=["x"])))


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 2))
        self.now += seconds


class PoliteFetcherTests(unittest.IsolatedAsyncioTestCase):
    def _fetcher(self, responses, **kw):
        clock = FakeTime()
        calls = []

        async def transport(url, headers, timeout):
            calls.append((url, headers))
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        f = PoliteFetcher(transport, min_interval=10, monotonic=clock.monotonic, sleep=clock.sleep, **kw)
        return f, clock, calls

    async def test_rate_limit_per_host(self):
        f, clock, calls = self._fetcher([HttpResponse(200, "a", {}), HttpResponse(200, "b", {})])
        self.assertEqual(await f.get("https://h.example/1"), "a")
        self.assertEqual(await f.get("https://h.example/2"), "b")
        self.assertEqual(clock.sleeps, [10.0])
        self.assertIn("telegram-market-monitor", calls[0][1]["User-Agent"])

    async def test_block_on_429_and_no_retry(self):
        f, clock, calls = self._fetcher([HttpResponse(429, "", {"retry-after": "120"})])
        with self.assertRaises(SourceBlocked) as ctx:
            await f.get("https://h.example/1")
        self.assertEqual(ctx.exception.retry_after_seconds, 120)
        with self.assertRaises(SourceBlocked):  # cool-down: no request is sent
            await f.get("https://h.example/2")
        self.assertEqual(len(calls), 1)

    async def test_captcha_page_is_a_block(self):
        f, _, _ = self._fetcher([HttpResponse(200, "<html>Please solve the CAPTCHA</html>", {})])
        with self.assertRaises(SourceBlocked):
            await f.get("https://h.example/1")

    async def test_retry_5xx_then_give_up(self):
        f, clock, calls = self._fetcher([HttpResponse(503, "", {}), OSError("reset"), HttpResponse(502, "", {})],
                                        max_retries=2)
        with self.assertRaises(SourceUnavailable):
            await f.get("https://h.example/1")
        self.assertEqual(len(calls), 3)

    async def test_retry_then_success(self):
        f, _, _ = self._fetcher([HttpResponse(500, "", {}), HttpResponse(200, "ok", {})])
        self.assertEqual(await f.get("https://h.example/1"), "ok")

    async def test_404_is_not_retried(self):
        f, _, calls = self._fetcher([HttpResponse(404, "", {})])
        with self.assertRaises(SourceUnavailable):
            await f.get("https://h.example/1")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
