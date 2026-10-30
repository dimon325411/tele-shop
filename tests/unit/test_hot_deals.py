"""Hot-deal mode: freshness, new sellers, AI pre-check of candidates, local Ollama provider."""

import unittest
from decimal import Decimal
from datetime import timedelta

from app.config import Settings
from app.services.ai import AIReply, OpenAICompatProvider, ScreenResult, build_provider, parse_screen, strip_thinking
from tests.unit.helpers import App, draft
from tests.unit.test_reseller import phone


class FakeScreener:
    def __init__(self, verdicts=None, fail=False):
        self.verdicts, self.fail, self.calls = verdicts or {}, fail, []

    async def screen_deal(self, listing, median, discount, sample):
        self.calls.append((listing.external_id, discount, sample))
        if self.fail:
            return None
        return self.verdicts.get(listing.external_id.split(":")[-1], ScreenResult("ok", "brak sygnałów ryzyka"))


class HotDealTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.user = await self.app.user(111)
        await self.app.admin.grant(999, 111, "pro", 60)
        self.search = await self.app.searches.create(self.user, draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"]))
        now = self.app.clock()
        for i, p in enumerate([3900, 3800, 4000, 3850, 3950, 3900]):
            self.app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=20)))
        await self.app.monitoring.check_search(self.search.id)  # baseline
        await self.app.repo.update_search(self.search.id, deal_min_discount=10)

    async def check(self):
        return await self.app.monitoring.check_search(self.search.id)

    async def test_stale_and_new_seller_deals_are_not_hot(self):
        now = self.app.clock()
        self.app.mock.publish(phone("fresh", 3400, posted=now - timedelta(minutes=20),
                                    seller_created_at=now - timedelta(days=900)))
        self.app.mock.publish(phone("bumped", 3400, posted=now - timedelta(hours=7)))
        self.app.mock.publish(phone("newseller", 3400, posted=now, seller_created_at=now - timedelta(days=3)))
        await self.app.repo.update_search(self.search.id, created_at=now - timedelta(days=2))
        out = await self.check()
        self.assertEqual((out.notifications, out.suppressed_not_hot), (1, 2))

    async def test_ai_rejects_red_flags_and_annotates_the_rest(self):
        screener = FakeScreener({"locked": ScreenResult("reject", "blokada iCloud w opisie"),
                                 "risky": ScreenResult("risk", "wymieniany ekran")})
        self.app.monitoring.screener = screener
        now = self.app.clock()
        for ext in ("good", "locked", "risky"):
            self.app.mock.publish(phone(ext, 3400, posted=now))
        out = await self.check()
        self.assertEqual((out.notifications, out.suppressed_ai), (2, 1))
        self.assertEqual(len(screener.calls), 3)
        self.assertEqual(screener.calls[0][1:], (13, 6))  # discount %, compared listings
        await self.app.notifications.deliver_batch()
        notes = {n.listing.external_id.split(":")[-1]: n.listing.ai_note for n in self.app.notifier.sent}
        self.assertEqual(notes, {"good": "✅ brak sygnałów ryzyka", "risky": "⚠️ wymieniany ekran"})

    async def test_ai_failure_never_blocks_a_deal(self):
        self.app.monitoring.screener = FakeScreener(fail=True)
        self.app.mock.publish(phone("cheap", 3400, posted=self.app.clock()))
        out = await self.check()
        self.assertEqual((out.notifications, out.suppressed_ai), (1, 0))

    async def test_ai_not_called_outside_deal_mode(self):
        screener = FakeScreener()
        self.app.monitoring.screener = screener
        await self.app.repo.update_search(self.search.id, deal_min_discount=None)
        self.app.mock.publish(phone("cheap", 3400, posted=self.app.clock()))
        out = await self.check()
        self.assertEqual((out.notifications, screener.calls), (1, []))


class ScreenDealTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_advisor_screen(self):
        from app.domain.models import StoredListing
        from app.services.ai import AdvisorService

        class Provider:
            def __init__(self, answer=None, error=None):
                self.answer, self.error, self.calls = answer, error, []

            async def complete(self, system, text, image_url=None):
                self.calls.append(text)
                if self.error:
                    raise self.error
                return AIReply(self.answer)

        listing = StoredListing(source="olx", external_id="1", url="https://www.olx.pl/d/oferta/x.html",
                                title="iPhone 15 Pro 128GB", description="Blokada iCloud, na części")
        ok = Provider('{"decyzja": "odrzuc", "powod": "blokada iCloud"}')
        advisor = AdvisorService(None, ok, Settings())
        self.assertEqual(await advisor.screen_deal(listing, 2050, 17, 9), ScreenResult("reject", "blokada iCloud"))
        self.assertIn("Blokada iCloud", ok.calls[0])
        self.assertIsNone(await AdvisorService(None, Provider(error=TimeoutError()), Settings())
                          .screen_deal(listing, 2050, 17, 9))
        self.assertIsNone(await AdvisorService(None, None, Settings()).screen_deal(listing, 2050, 17, 9))


class ParseTests(unittest.TestCase):
    def test_parse_screen(self):
        self.assertEqual(parse_screen('{"decyzja": "ryzyko", "powod": "wymieniany  ekran"}'),
                         ScreenResult("risk", "wymieniany ekran"))
        self.assertEqual(parse_screen('<think>hmm {"x":1}</think>\nOdpowiedź: {"decyzja": "OK", "powod": ""}').decision,
                         "ok")
        self.assertIsNone(parse_screen("Nie wiem"))
        self.assertIsNone(parse_screen('{"decyzja": "może"}'))

    def test_strip_thinking(self):
        self.assertEqual(strip_thinking("<think>a\nb</think>\n\nWarto."), "Warto.")
        self.assertEqual(strip_thinking("a</think>Warto."), "Warto.")


class OllamaProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_build(self):
        p = build_provider(Settings(ai_provider="ollama", ai_model="qwen3:8b"))
        self.assertIsInstance(p, OpenAICompatProvider)
        self.assertEqual(p.url, "http://localhost:11434/v1/chat/completions")
        self.assertFalse(p.vision)
        self.assertIsNone(build_provider(Settings(ai_provider="ollama")))  # model name required
        self.assertTrue(Settings(ai_provider="ollama", ai_model="qwen3:8b").ai_ready)

    async def test_text_only_request_without_key(self):
        sent = {}

        async def transport(url, headers, body, timeout):
            sent.update(url=url, headers=headers, body=body)
            return 200, {"choices": [{"message": {"content": "<think>...</think>Warto kupić."}}]}

        p = OpenAICompatProvider("http://localhost:11434/v1", "qwen3:8b", transport=transport, vision=False)
        reply = await p.complete("sys", "pytanie", "https://img.example/1.jpg")
        self.assertEqual(reply, AIReply("Warto kupić.", 0, 0))
        self.assertEqual(sent["body"]["messages"][1]["content"], "pytanie\n/no_think")  # text only; qwen3: no long reasoning
        self.assertNotIn("authorization", sent["headers"])


if __name__ == "__main__":
    unittest.main()


class PackTests(unittest.IsolatedAsyncioTestCase):
    async def test_pack_is_created_once_and_respects_plan_limit(self):
        from app.config import PaymentsMode
        from app.domain.reseller_pack import PACK, DEAL_PERCENT
        from app.domain.matcher import match
        from app.domain.models import Listing

        settings = Settings(admin_ids=[999], payments_mode=PaymentsMode.TEST, enabled_sources=["mock", "olx"],
                            personal_mode=True)
        app = await App(settings=settings, enabled=["mock", "olx"]).setup()
        user = await app.user(999)
        created, present, error = await app.searches.create_pack(user)
        self.assertIsNone(error, error and error.code)
        self.assertEqual(len(created), len(PACK))
        searches = await app.searches.list(user)
        self.assertTrue(all(s.criteria.deal_min_discount == DEAL_PERCENT and s.sources == ["olx"] for s in searches))
        again = await app.searches.create_pack(user)
        self.assertEqual((again[0], len(again[1]), again[2]), ([], len(PACK), None))
        by_name = {s.name: s.criteria for s in searches}

        def ok(name, title, description=None):
            return match(Listing(source="olx", external_id="1", url="https://www.olx.pl/d/oferta/x.html",
                                 title=title, description=description, price=Decimal(5000), condition="used",
                                 category=by_name[name].category), by_name[name]).matched

        self.assertTrue(ok("PlayStation 5", "Konsola PlayStation 5 Slim", "w zestawie pad i gra"))
        self.assertTrue(ok("PlayStation 5", "PS5 z napędem"))
        self.assertFalse(ok("PlayStation 5", "PS5 Pro 2TB"))
        self.assertTrue(ok("iPhone 15", "iPhone 15 128GB", "plus etui gratis"))
        self.assertFalse(ok("iPhone 15", "iPhone 15 Plus 128GB"))
        self.assertFalse(ok("iPhone 15 Pro", "iPhone 15 Pro Max 256GB"))
        self.assertTrue(ok("MacBook Air M1", "Apple MacBook Air 13 M1 8/256"))
        self.assertFalse(ok("RTX 4070", "Gigabyte RTX 4070 Super"))

    async def test_pack_stops_at_plan_limit(self):
        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "basic", 30)
        created, _, error = await app.searches.create_pack(user)
        self.assertIsNotNone(error)
        self.assertIn(error.code, {"search_limit", "source_disabled", "source_not_in_plan"})


class DigestTests(unittest.IsolatedAsyncioTestCase):
    async def test_evening_digest_once_per_day_with_near_misses(self):
        from app.config import PaymentsMode
        from app.worker import jobs
        from tests.unit.test_controller_jobs import container

        settings = Settings(admin_ids=[999], payments_mode=PaymentsMode.TEST, enabled_sources=["mock"],
                            personal_mode=True, digest_hour=21)
        app = await App(settings=settings).setup()
        user = await app.user(999)
        search = await app.searches.create(user, draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"]))
        await app.repo.update_search(search.id, deal_min_discount=15)
        now = app.clock()
        for i, p in enumerate([3900, 3800, 4000, 3850, 3950, 3900]):
            app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=20)))
        await jobs.run_check(container(app), search.id)  # baseline: silent for hot-deal searches
        self.assertEqual(app.notifier.texts, [])
        app.clock.advance(hours=1)
        app.mock.publish(phone("near", 3500, posted=app.clock()))  # −10 %: below the 15 % threshold
        app.mock.publish(phone("normal", 3900, posted=app.clock()))
        await app.monitoring.check_search(search.id)
        c = container(app)
        c.monitoring.clock = app.clock
        self.assertEqual(await jobs.daily_digest(c), 0)  # 13:00 in Warsaw: too early
        app.clock.advance(hours=9)  # 22:00 in Warsaw
        self.assertEqual(await jobs.daily_digest(c), 1)
        self.assertEqual(await jobs.daily_digest(c), 0)  # once a day
        text = app.notifier.texts[-1][1]
        self.assertIn("nowych pasujących ogłoszeń dziś: 2", text)
        self.assertIn("−10% · iPhone 15 Pro", text)
        self.assertIn("https://example.invalid/test-listing/near", text)
        self.assertNotIn("a0", text)  # the baseline market is not "new today"

    async def test_digest_off_in_commercial_mode(self):
        from app.worker import jobs
        from tests.unit.test_controller_jobs import container

        app = await App().setup()
        self.assertEqual(await jobs.daily_digest(container(app)), 0)


class PersonalPlanSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_stored_personal_limit_follows_code(self):
        app = await App().setup()
        await app.repo.update_plan("personal", max_active_searches=20)  # state saved by an older version
        await app.subs.seed_plans()
        self.assertEqual((await app.repo.get_plan_by_code("personal")).max_active_searches, 50)
        await app.repo.update_plan("pro", max_active_searches=7)  # admin changes to sold plans stay
        await app.subs.seed_plans()
        self.assertEqual((await app.repo.get_plan_by_code("pro")).max_active_searches, 7)
