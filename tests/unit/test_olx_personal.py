"""OLX personal-mode adapter: parser on a synthetic sample, URL building, block handling, mode gating."""

import json
import unittest
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import PaymentsMode, Settings
from app.domain.models import SearchCriteria
from app.sources.base import SourceBlocked, SourceDisabled, SourceLayoutChanged
from app.sources.disabled import OlxSource
from app.sources.http import HttpResponse, PoliteFetcher
from app.sources.olx import OlxPersonalSource, build_queries, build_url, parse_offers
from app.sources.registry import SourceRegistry
from tests.unit.helpers import App, draft

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "olx_offers_synthetic.json"


class ParserTests(unittest.TestCase):
    def test_parse_synthetic_sample(self):
        listings = parse_offers(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual([l.external_id for l in listings], ["900000001", "900000002"])  # otodom + no-url skipped
        first, second = listings
        self.assertEqual(first.title, "Rower górski Kross Hexagon 29")
        self.assertEqual((first.price, first.currency), (Decimal("1200"), "PLN"))
        self.assertEqual((first.city, first.region, first.condition), ("Kraków", "Małopolskie", "used"))
        self.assertTrue(first.delivery_available)
        self.assertEqual((first.latitude, first.longitude), (50.06, 19.94))
        self.assertEqual(first.posted_at.isoformat(), "2026-10-24T12:01:00+02:00")
        self.assertFalse(first.is_test)  # real source => never flagged as test
        self.assertTrue(first.url.startswith("https://www.olx.pl/d/oferta/"))
        self.assertIsNone(second.price)  # "Za darmo" (0) is not a price
        self.assertEqual(second.condition, "new")
        self.assertFalse(second.delivery_available)

    def test_layout_changes_are_detected(self):
        for bad in ["<html>blocked</html>", "{}", '{"data": "x"}',
                    json.dumps({"data": [{"foo": 1}, {"bar": 2}]})]:
            with self.assertRaises(SourceLayoutChanged, msg=bad):
                parse_offers(bad)
        self.assertEqual(parse_offers('{"data": []}'), [])  # empty results are fine

    def test_queries_and_url(self):
        c = SearchCriteria(keywords=["rower", "kross"], price_min=Decimal("100"), price_max=Decimal("999.5"))
        self.assertEqual(build_queries(c), ["rower kross"])
        self.assertEqual(build_queries(SearchCriteria(alt_keywords=["a", "b", "c", "d"])), ["a", "b", "c"])
        url = urlparse(build_url("rower kross", c))
        q = parse_qs(url.query)
        self.assertEqual(url.netloc + url.path, "www.olx.pl/api/v1/offers/")
        self.assertEqual(q["query"], ["rower kross"])
        self.assertEqual(q["sort_by"], ["created_at:desc"])
        self.assertEqual(q["filter_float_price:from"], ["100"])
        self.assertEqual(q["filter_float_price:to"], ["1000"])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def _source(self, responses):
        calls = []

        async def transport(url, headers, timeout):
            calls.append(url)
            return responses.pop(0)

        async def no_sleep(_):
            return None

        fetcher = PoliteFetcher(transport, min_interval=0, sleep=no_sleep)
        return OlxPersonalSource(fetcher), calls

    async def test_fetch_dedupes_across_queries(self):
        body = FIXTURE.read_text(encoding="utf-8")
        src, calls = self._source([HttpResponse(200, body, {}), HttpResponse(200, body, {})])
        result = await src.fetch(SearchCriteria(alt_keywords=["rower", "kross"]))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result), 2)

    async def test_block_stops_without_retry(self):
        src, calls = self._source([HttpResponse(403, "Forbidden", {})])
        with self.assertRaises(SourceBlocked):
            await src.fetch(SearchCriteria(keywords=["rower"]))
        with self.assertRaises(SourceBlocked):  # cool-down: no second request
            await src.fetch(SearchCriteria(keywords=["rower"]))
        self.assertEqual(len(calls), 1)


class ModeTests(unittest.IsolatedAsyncioTestCase):
    def test_commercial_mode_keeps_olx_disabled(self):
        reg = SourceRegistry.default(["mock", "olx"], personal_mode=False)
        self.assertIsInstance(reg.get("olx"), OlxSource)
        settings = Settings(payments_mode=PaymentsMode.TEST)
        self.assertTrue(settings.payments_available)
        self.assertEqual(settings.real_sources_allowed, set())

    def test_personal_mode_enables_olx_and_disables_payments(self):
        reg = SourceRegistry.default(["mock"], personal_mode=True)
        self.assertIsInstance(reg.get("olx"), OlxPersonalSource)
        self.assertTrue(reg.is_enabled("olx"))
        settings = Settings(payments_mode=PaymentsMode.TEST, personal_mode=True)
        self.assertFalse(settings.payments_available)

    async def test_personal_mode_owner_only(self):
        settings = Settings(admin_ids=[999], personal_mode=True, enabled_sources=["mock"])
        app = await App(settings=settings).setup()
        owner, _ = await app.users.register(999, "owner", "Owner")
        stranger, _ = await app.users.register(5, "x", "X")
        owner_sub = await app.repo.get_active_subscription(owner.id)
        plan = await app.repo.get_plan(owner_sub.plan_id)
        self.assertEqual(plan.code, "personal")
        self.assertIsNone(await app.repo.get_active_subscription(stranger.id))  # no trial for strangers
        s = await app.searches.create(owner, draft(interval_seconds=60))
        self.assertEqual(s.interval_seconds, 600)  # never faster than 10 minutes
        self.assertNotIn("personal", [p.code for p in await app.repo.list_plans()])  # never offered
        with self.assertRaises(Exception):
            await app.payments.create_invoice(owner, "basic")

    async def test_commercial_disabled_stub_never_fetches(self):
        with self.assertRaises(SourceDisabled):
            await OlxSource().fetch(SearchCriteria(keywords=["x"]))


if __name__ == "__main__":
    unittest.main()


class PersonalUxTests(unittest.IsolatedAsyncioTestCase):
    def test_wizard_skips_category_for_olx_only(self):
        from app.wizard import SearchWizard

        reg = SourceRegistry.default(["mock"], personal_mode=True)
        w = SearchWizard(reg)
        state, _ = w.start(None)
        w.on_text(state, "Rowery")
        w.on_callback(state, "wz:src:mock")  # deselect preselected? (two sources -> nothing preselected)
        state["draft"]["sources"] = ["olx"]
        w.on_callback(state, "wz:src_done")
        w.on_text(state, "rower")
        w.on_callback(state, "wz:skip")
        w.on_callback(state, "wz:skip")
        self.assertEqual(state["step"], "price")  # category step skipped: OLX gives no categories
        state2, _ = w.start(None)
        w.on_text(state2, "Mix")
        state2["draft"]["sources"] = ["olx", "mock"]
        w.on_callback(state2, "wz:src_done")
        w.on_text(state2, "rower")
        w.on_callback(state2, "wz:skip")
        w.on_callback(state2, "wz:skip")
        self.assertEqual(state2["step"], "category")  # mock listings have categories

    async def test_personal_welcome_after_silent_registration(self):
        from app.controller import Controller
        from tests.unit.test_controller_jobs import container

        settings = Settings(admin_ids=[999], personal_mode=True, enabled_sources=["mock"])
        app = await App(settings=settings).setup()
        await app.users.register(999, "owner", "Owner")  # first message was "Hello", not /start
        reply = await Controller(container(app)).start(999, "owner", "Owner")
        self.assertIn("prywatny bot", reply.text)


class PromotedTests(unittest.TestCase):
    def test_promoted_positions_are_skipped(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["metadata"]["source"] = {"promoted": [0], "organic": [1, 2, 3]}
        ids = [l.external_id for l in parse_offers(data)]
        self.assertEqual(ids, ["900000002"])
        data["data"] = data["data"][:1]
        self.assertEqual(parse_offers(data), [])  # only an ad on the page is not a layout change
