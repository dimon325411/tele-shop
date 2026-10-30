import json
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.analytics import Analytics
from app.domain.models import Listing, Search, SearchCriteria
from app.domain.variants import is_junk, variant_of
from app.intents import detect
from app.sources.http import HttpResponse, PoliteFetcher
from app.sources.olx import OlxPersonalSource, build_url, parse_offers
from app.sources.olx_categories import OlxCategories, load_ids
from tests.unit.helpers import App, draft
from tests.unit.test_controller_jobs import container

D = Decimal
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "olx_offers_synthetic.json"


def phone(ext, price, title="iPhone 15 Pro 256GB", condition="used", posted=None, **kw):
    return Listing(source="mock", external_id=ext, url=f"https://example.invalid/test-listing/{ext}",
                   title=f"[TEST] {title}", price=D(price), condition=condition, posted_at=posted, **kw)


class VariantTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(variant_of("iPhone 15 Pro 256GB tytan"), "256gb")
        self.assertEqual(variant_of("iPhone 15 Pro 256 GB"), "256gb")
        self.assertEqual(variant_of("iPhone 15 Pro 1 TB"), "1tb")
        self.assertIsNone(variant_of("iPhone 15 Pro"))

    def test_junk(self):
        for title in ["iPhone 15 Pro zbity ekran", "iPhone 15 Pro icloud", "Etui iPhone 15 Pro", "Kupię iPhone 15",
                      "Zamienię PS5 na Xbox", "RTX 3070 na części"]:
            self.assertTrue(is_junk(title), title)
        for title in ["iPhone 15 Pro 256GB + etui + szkło", "iPhone 15 Pro bateria 100%", "PS5 z padem"]:
            self.assertFalse(is_junk(title), title)


class CategoryTests(unittest.IsolatedAsyncioTestCase):
    def test_url_and_parse_with_category(self):
        cats = OlxCategories({"konsole": [1022, 1023], "iphone": [2001]})
        q = parse_qs(urlparse(build_url("ps5", SearchCriteria(keywords=["ps5"]), 40, 1022)).query)
        self.assertEqual(q["category_id"], ["1022"])
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["data"][0]["category"] = {"id": 2001}
        data["data"][1]["category"] = {"id": 999}
        first, second = parse_offers(data, cats)
        self.assertEqual((first.category, second.category), ("iphone", None))

    async def test_fetch_queries_each_category_of_preset(self):
        calls = []

        async def transport(url, headers, timeout):
            calls.append(url)
            return HttpResponse(200, '{"data": []}', {})

        async def no_sleep(_):
            return None

        src = OlxPersonalSource(PoliteFetcher(transport, min_interval=0, sleep=no_sleep),
                                OlxCategories({"konsole": [1022, 1023]}))
        await src.fetch(SearchCriteria(keywords=["playstation 5"], category="konsole"))
        self.assertEqual([parse_qs(urlparse(u).query)["category_id"] for u in calls], [["1022"], ["1023"]])
        calls.clear()
        await src.fetch(SearchCriteria(keywords=["iphone"], category="iphone"))  # ids unknown -> no filter
        self.assertNotIn("category_id", parse_qs(urlparse(calls[0]).query))

    async def test_other_sections_are_dropped_locally(self):
        async def transport(url, headers, timeout):
            return HttpResponse(200, json.dumps({"data": [
                {"id": 1, "url": "https://www.olx.pl/d/oferta/a.html", "title": "RTX 3070", "category": {"id": 2184}},
                {"id": 2, "url": "https://www.olx.pl/d/oferta/b.html", "title": "PC z RTX 3070", "category": {"id": 1197}},
            ]}), {})

        async def no_sleep(_):
            return None

        src = OlxPersonalSource(PoliteFetcher(transport, min_interval=0, sleep=no_sleep),
                                OlxCategories({"karty_graficzne": [2184], "komputery": [1197]}))
        got = await src.fetch(SearchCriteria(keywords=["rtx 3070"], category="karty_graficzne"))
        self.assertEqual([l.external_id for l in got], ["1"])

    def test_missing_file(self):
        self.assertEqual(load_ids(Path("/nonexistent.json")), {})


class MarketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.user = await self.app.user(111)
        await self.app.admin.grant(999, 111, "pro", 60)
        self.search = await self.app.searches.create(self.user, draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"]))
        now = self.app.clock()
        prices_256 = [3900, 3800, 4000, 3850, 3950, 3900]
        for i, p in enumerate(prices_256):
            self.app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=20)))
        for i, p in enumerate([5200, 5100, 5300, 5250, 5150]):
            self.app.mock.publish(phone(f"b{i}", p, title="iPhone 15 Pro 512GB", posted=now - timedelta(days=20)))
        self.app.mock.publish(phone("junk", 1500, title="iPhone 15 Pro 256GB zbity ekran", posted=now))
        await self.app.monitoring.check_search(self.search.id)  # baseline
        self.market = self.app.monitoring.market

    async def test_variant_aware_median_ignores_junk(self):
        listing = next(l for l in self.app.repo.listings.values() if l.external_id == "a0")
        stats = await self.market.stats_for_listing(self.search.id, listing)
        self.assertEqual((stats.median, stats.variant), (D(3900), "256gb"))  # 512 GB and the broken one excluded

    async def test_deal_mode_notifies_only_deals(self):
        app = self.app
        await app.repo.update_search(self.search.id, deal_min_discount=10)
        now = app.clock()
        app.mock.publish(phone("cheap", 3400, posted=now))             # −13 % -> deal
        app.mock.publish(phone("normal", 3850, posted=now))            # −1 %  -> not a deal
        app.mock.publish(phone("broken", 2000, title="iPhone 15 Pro 256GB pęknięty", posted=now))  # junk
        app.mock.publish(phone("cheap512", 4400, title="iPhone 15 Pro 512GB", posted=now))  # −15 % in 512 market
        out = await app.monitoring.check_search(self.search.id)
        self.assertEqual(out.notifications, 2)
        self.assertEqual(out.suppressed_not_deal, 2)
        await app.notifications.deliver_batch()
        self.assertEqual(sorted(n.listing.external_id for n in app.notifier.sent), ["cheap", "cheap512"])
        cheap = next(n for n in app.notifier.sent if n.listing.external_id == "cheap")
        self.assertEqual(cheap.price_stats.variant, "256gb")

    async def test_find_deals_and_analytics_text(self):
        now = self.app.clock()
        self.app.mock.publish(phone("cheap", 3400, posted=now))
        await self.app.monitoring.check_search(self.search.id)
        deals = await self.market.find_deals(await self.app.repo.get_search(self.search.id), 10)
        self.assertEqual([d.listing.external_id for d in deals], ["cheap"])
        self.assertEqual((deals[0].discount, deals[0].potential_profit), (13, D(500)))
        text, buttons = await Analytics(container(self.app)).deals(self.user, [self.search], None)
        self.assertIn("−13%", text)
        self.assertIn("256 GB", text)
        self.assertIn("zysk ~500", text)
        self.assertIn('<a href="https://example.invalid/test-listing/cheap">', text)  # link in the text itself
        self.assertEqual(buttons, [])
        text, _ = await Analytics(container(self.app)).deals(self.user, [self.search], 30)
        self.assertIn("brak ofert z ostatnich 24 h tańszych o min. 30%", text)

    async def test_snapshots_and_charts(self):
        app = self.app
        an = Analytics(container(app))
        first = await an.chart(self.user, self.search, "256gb")  # 1 day only -> distribution of current prices
        self.assertTrue(first.png.startswith(b"\x89PNG"))
        self.assertIn("Historia zbierana od", first.caption)
        for day in range(1, 4):
            app.clock.advance(days=1)
            app.mock.publish(phone(f"d{day}", 3700 - day * 50, posted=app.clock()))
            await app.monitoring.check_search(self.search.id)
            await app.monitoring.market.take_snapshot(self.search.id)
        rows = await app.monitoring.market.history(self.search.id, "256gb")
        self.assertEqual(len(rows), 4)
        chart = await an.chart(self.user, self.search, "256gb")
        self.assertTrue(chart.png.startswith(b"\x89PNG"))
        self.assertIn("mediana", chart.caption)
        self.assertIn("w 3 dni", chart.caption)
        options = await an.chart_options(self.user, self.search)
        self.assertEqual([b.callback for row in options for b in row][:2],
                         [f"ch:{self.search.id}:256gb", f"ch:{self.search.id}:512gb"])

    async def test_no_data_message(self):
        empty = await self.app.searches.create(self.user, draft(name="Xbox", keywords=["xbox"]))
        result = await Analytics(container(self.app)).chart(self.user, empty)
        self.assertIsNone(result.png)
        self.assertIn("Za mało danych", result.text)


class IntentTests(unittest.TestCase):
    def setUp(self):
        mk = lambda i, n, k: Search(id=i, user_id=1, name=n, sources=["olx"], criteria=SearchCriteria(keywords=k),
                                    interval_seconds=600)
        self.searches = [mk(1, "iPhone 15 Pro", ["iphone 15 pro"]), mk(2, "iPhone 13", ["iphone 13"]),
                         mk(3, "PS5", ["playstation 5"]), mk(4, "RTX 3070", ["rtx 3070"])]

    def test_russian_and_polish(self):
        cases = {
            "покажи график изменения цены на айфон 15 про": ("chart", ["iPhone 15 Pro"], None),
            "покажи айфон на котором мы заработаем": ("deals", ["iPhone 15 Pro", "iPhone 13"], None),
            "айфон 13 выгодно спред 15%": ("deals", ["iPhone 13"], 15),
            "wykres ps5": ("chart", ["PS5"], None),
            "okazje rtx 3070": ("deals", ["RTX 3070"], None),
            "плейстейшн график": ("chart", ["PS5"], None),
        }
        for text, (intent, names, pct) in cases.items():
            i, c, p = detect(text, self.searches)
            self.assertEqual((i, [s.name for s in c], p), (intent, names, pct), text)

    def test_unrelated(self):
        self.assertEqual(detect("привет", self.searches), (None, [], None))
        self.assertEqual(detect("график macbook", self.searches)[1], [])


class WizardDealToggleTests(unittest.TestCase):
    def test_cycle(self):
        from app.sources.registry import SourceRegistry
        from app.wizard import SearchWizard

        w = SearchWizard(SourceRegistry.default(["mock"]))
        state, _ = w.start(None)
        state["step"] = "summary"
        state["draft"].update(name="x", sources=["mock"], keywords=["iphone"])
        seen = []
        for _ in range(5):
            w.on_callback(state, "wz:deal")
            seen.append(state["draft"]["deal_min_discount"])
        self.assertEqual(seen, [10, 15, 20, 30, None])
        w.on_callback(state, "wz:deal")
        self.assertEqual(w.draft(state).deal_min_discount, 10)
