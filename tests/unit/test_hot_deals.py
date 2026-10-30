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
        discount, sample = screener.calls[0][1:]  # judged against the complete market of this check
        self.assertGreaterEqual(discount, 10)
        self.assertGreaterEqual(sample, 6)
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


class ManySearchesTests(unittest.IsolatedAsyncioTestCase):
    """50 searches (the reseller pack): every message must fit into Telegram's 4096 characters."""

    async def asyncSetUp(self):
        from app.config import PaymentsMode

        settings = Settings(admin_ids=[999], payments_mode=PaymentsMode.TEST, enabled_sources=["mock", "olx"],
                            personal_mode=True)
        self.app = await App(settings=settings, enabled=["mock", "olx"]).setup()
        self.user = await self.app.user(999)
        await self.app.searches.create(self.user, draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"]))
        created, present, error = await self.app.searches.create_pack(self.user)
        self.assertIsNone(error)
        self.assertEqual(len(created) + len(present), 50)

    async def test_search_list_is_paged(self):
        from app.controller import Controller
        from tests.unit.test_controller_jobs import container

        ctl = Controller(container(self.app))
        first = await ctl.list_searches(self.user)
        self.assertLess(len(first.text), 4096)
        self.assertIn("Strona 1/7", first.text)
        callbacks = [b.callback for row in first.buttons for b in row if b.callback]
        self.assertIn("sl:1", callbacks)
        self.assertNotIn("sl:-1", callbacks)
        last = await ctl.list_searches(self.user, 99)  # clamped to the last page
        self.assertIn("Strona 7/7", last.text)
        self.assertLess(len(last.text), 4096)

    async def test_digest_and_deals_fit(self):
        from app.analytics import Analytics
        from app.services.digest import DigestService
        from tests.unit.test_controller_jobs import container

        app = self.app
        for s in await app.searches.list(self.user):  # every search sees a few new listings today
            for i in range(3):
                await app.repo.add_match(s.id, (await app.repo.upsert_listing(
                    phone(f"{s.id}-{i}", 1000 + i, title=f"{s.name} {i}", posted=app.clock()),
                    app.clock() + timedelta(hours=1))).listing.id, self.user.id, [])
        text = await DigestService(app.repo, app.monitoring.market, app.clock).build(
            self.user, app.clock() + timedelta(hours=2))
        self.assertLess(len(text), 4096)
        self.assertIn("…i jeszcze 35 wyszukiwań", text)
        deals_text, _ = await Analytics(container(app)).deals(self.user, await app.searches.list(self.user))
        self.assertIn("wszystkie wyszukiwania (50)", deals_text)

    def test_split_message(self):
        from app.presentation import split_message

        text = "\n".join(f"<b>linia {i}</b> " + "x" * 80 for i in range(200))
        parts = split_message(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4096 for p in parts))
        self.assertEqual("\n".join(parts), text)  # nothing lost, tags never cut
        self.assertEqual(split_message("krótko"), ["krótko"])


class StatsCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_and_navigation(self):
        from app.analytics import Analytics
        from app.intents import detect
        from tests.unit.test_controller_jobs import container

        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "pro", 60)
        d = draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"])
        d.category = None
        search = await app.searches.create(user, d)
        await app.repo.update_search(search.id, deal_min_discount=15)
        search = await app.repo.get_search(search.id)
        now = app.clock()
        for i, p in enumerate([3900, 3800, 4000, 3850, 3950, 3900]):
            app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=3)))
        for i, p in enumerate([5200, 5100]):
            app.mock.publish(phone(f"b{i}", p, title="iPhone 15 Pro 512GB", posted=now))
        app.mock.publish(phone("broken", 900, title="iPhone 15 Pro 256GB zbity ekran", posted=now))
        await app.monitoring.check_search(search.id)
        an = Analytics(container(app))
        text, buttons = await an.stats(user, search)
        text = text.replace("\xa0", " ")
        self.assertNotIn("zbity", text)  # the broken one is not market
        self.assertIn("<b>256 GB</b> · mediana <b>3 900 PLN</b> · typowo 3 862–3 938 · 6 ofert", text)
        self.assertIn("🔥 okazja (−15%): ≤ <b>3 315 PLN</b>", text)
        self.assertIn("<b>512 GB</b> · 2 ofert — za mało do mediany", text)
        # 3-day-old offers feed the median but are never linked; only offers from the last 24 h are
        self.assertNotIn("test-listing/a1", text)
        self.assertIn("• <b>5 100 PLN</b> · 512 GB · 0 min temu (24.10 12:00)\n   <a href=\"https://example.invalid/test-listing/b1\">",
                      text)
        self.assertEqual([b.callback for b in buttons[0]], [f"chs:{search.id}", f"ai:c:{search.id}"])
        _, rows = await an.nav(user, "st")  # few searches: models right away
        self.assertEqual(rows[0][0].callback, f"st:s:{search.id}")
        _, rows = await an.nav(user, "ch")
        self.assertEqual(rows[0][0].callback, f"chs:{search.id}")
        intent, found, _ = detect("статистика айфон 15 про", [search])
        self.assertEqual((intent, found), ("stats", [search]))
        self.assertEqual(detect("mediana iphone 15 pro", [search])[0], "stats")


class NavigationTests(ManySearchesTests):
    async def test_category_then_model_for_every_tool(self):
        from app.analytics import Analytics
        from app.controller import Controller, menu_rows
        from tests.unit.test_controller_jobs import container

        an = Analytics(container(self.app))
        text, rows = await an.nav(self.user, "dl")
        self.assertIn("Okazje", text)
        flat = [b.callback for row in rows for b in row]
        self.assertEqual(flat[0], "dl:all")
        self.assertIn("nav:dl:karty_graficzne", flat)
        self.assertEqual(flat[-1], "menu:home")
        _, rows = await an.nav(self.user, "dl", "karty_graficzne")
        flat = [b.callback for row in rows for b in row]
        self.assertEqual(flat[0], "dlc:karty_graficzne")
        self.assertEqual(len([c for c in flat if c.startswith("dl:")]), 16)
        self.assertEqual(flat[-1], "nav:dl")
        for mode, leaf in (("st", "st:s:"), ("ch", "chs:"), ("ai", "ai:c:")):
            _, rows = await an.nav(self.user, mode, "iphone")
            self.assertTrue(rows[0][0].callback.startswith(leaf), mode)
            self.assertEqual(sum(len(r) for r in rows[:-1]), 15)  # 15 pack iPhones (the manual one has no category)
        menu = [b.callback for row in menu_rows("pl", personal=True) for b in row]
        self.assertEqual(menu, ["nav:dl", "nav:st", "menu:searches", "menu:new", "settings:open", "menu:help"])
        settings_cbs = [b.callback for row in Controller(container(self.app)).settings_view(self.user).buttons
                        for b in row]
        self.assertIn("menu:pack", settings_cbs)
        self.assertIn("menu:status", settings_cbs)
        self.assertNotIn("menu:plan", menu)  # nothing to buy in personal mode
        card_text, card = await an.stats(self.user, (await self.app.searches.list(self.user))[0])
        cbs = [b.callback for row in card for b in row]
        self.assertTrue(any(c.startswith("ai:c:") for c in cbs) and any(c.startswith("s:pause:") for c in cbs))
        self.assertTrue(any(c.startswith("chs:") for c in cbs) and any(c.startswith("nav:st:") for c in cbs))


class FreshOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_cheap_listing_is_statistics_only(self):
        from app.presentation import fmt_posted

        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "pro", 60)
        search = await app.searches.create(user, draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"]))
        now = app.clock()
        for i, p in enumerate([3900, 3800, 4000, 3850, 3950, 3900]):
            app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=3)))
        app.mock.publish(phone("old_cheap", 3000, posted=now - timedelta(hours=30)))
        app.mock.publish(phone("new_cheap", 3300, posted=now - timedelta(hours=2)))
        await app.monitoring.check_search(search.id)
        deals = await app.monitoring.market.find_deals(await app.repo.get_search(search.id), 10)
        self.assertEqual([d.listing.external_id for d in deals], ["new_cheap"])
        old = next(l for l in app.repo.listings.values() if l.external_id == "old_cheap")
        self.assertTrue(fmt_posted(old, now, "Europe/Warsaw").startswith("30 godz. temu (23.10"))
        self.assertIn("3 dni temu", fmt_posted(next(l for l in app.repo.listings.values()
                                                       if l.external_id == "a0"), now, "Europe/Warsaw"))



class BaselineHotTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_bargain_found_by_the_first_check_is_sent(self):
        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "pro", 60)
        d = draft(name="RTX 3060", keywords=["iphone 15 pro"])
        d.deal_min_discount = 15
        search = await app.searches.create(user, d)
        now = app.clock()
        for i, p in enumerate([3900, 3800, 4000, 3850, 3950, 3900]):
            app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=3)))
        app.mock.publish(phone("fresh_bargain", 3000, posted=now - timedelta(hours=4)))
        app.mock.publish(phone("old_bargain", 3000, posted=now - timedelta(hours=30)))
        out = await app.monitoring.check_search(search.id)
        self.assertTrue(out.baseline)
        self.assertEqual((out.notifications, out.suppressed_not_hot), (1, 7))
        await app.notifications.deliver_batch()
        self.assertEqual([n.listing.external_id.split(":")[-1] for n in app.notifier.sent], ["fresh_bargain"])



class PackSyncAndSuspiciousTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_pack_search_gets_new_filters_and_market_follows(self):
        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "pro", 60)
        d = draft(name="MacBook Pro M1", keywords=["macbook pro", "m1"])
        d.deal_min_discount = 15
        search = await app.searches.create(user, d)
        now = app.clock()
        for i, p in enumerate([2500, 2600, 2550, 2450, 2500]):
            app.mock.publish(phone(f"m{i}", p, title="MacBook Pro 13 M1 512GB", posted=now - timedelta(days=2)))
        for i, p in enumerate([4200, 4300, 4100]):
            app.mock.publish(phone(f"p{i}", p, title="MacBook Pro 14 M1 Pro 512GB", posted=now - timedelta(days=2)))
        await app.monitoring.check_search(search.id)
        self.assertEqual(len(await app.monitoring.market.market(search.id)), 8)
        self.assertEqual(await app.searches.sync_pack(user), 1)
        criteria = (await app.repo.get_search(search.id)).criteria
        self.assertEqual(criteria.excluded_keywords, ["m1 pro", "m1 max"])
        self.assertEqual(criteria.category, "laptopy")
        market = await app.monitoring.market.market(search.id)
        self.assertEqual(sorted(l.external_id.split(":")[-1] for l in market), [f"m{i}" for i in range(5)])
        self.assertEqual(await app.searches.sync_pack(user), 0)  # idempotent

    def test_suspicious_discount_is_flagged(self):
        from app.domain.pricing import PriceStats
        from app.presentation import render_listing_notification
        from app.services.notifications import OutgoingListing
        from app.domain.models import StoredListing, User
        from decimal import Decimal as D

        now = timedelta(0)
        from tests.unit.helpers import T0
        listing = StoredListing(source="olx", external_id="1", url="https://www.olx.pl/d/oferta/x.html",
                                title="RTX 5070", price=D(1429), currency="PLN", posted_at=T0, id=1)
        user = User(id=1, telegram_id=1)
        item = OutgoingListing(user, listing, 1, "RTX 5070", "new", None,
                               PriceStats(D(3774), D(3500), D(4000), 20))
        text, _ = render_listing_notification(item, T0)
        self.assertIn("−62%", text)
        self.assertIn("Bardzo tanio", text)
        item.price_stats = PriceStats(D(1700), D(1600), D(1800), 20)
        self.assertNotIn("Bardzo tanio", render_listing_notification(item, T0)[0])


class JunkFromLiveDataTests(unittest.TestCase):
    def test_reserved_and_multi_model_titles(self):
        from app.domain.variants import is_junk

        for title in ["Iphone 17 PRO MAX Zarezerwowane", "Iphone 15 pro MAX iphone 16 pro iphone 16 pro MAX",
                      "PS5 sprzedane"]:
            self.assertTrue(is_junk(title), title)
        for title in ["iPhone 15 Pro 256GB", "Gigabyte RTX 3060 12GB", "PC i5 RTX 4070 32GB RAM"]:
            self.assertFalse(is_junk(title), title)


class DealAreaTests(unittest.IsolatedAsyncioTestCase):
    def test_in_area(self):
        from app.domain.area import area_label, distance_km, in_area
        from app.domain.models import Listing, User

        user = User(id=1, telegram_id=1, deal_city="krakow", deal_radius_km=30)
        wieliczka = Listing(source="olx", external_id="1", url="u", title="t", city="Wieliczka",
                            latitude=49.987, longitude=20.065)
        katowice = Listing(source="olx", external_id="2", url="u", title="t", city="Katowice",
                           latitude=50.2649, longitude=19.0238)
        no_coords = Listing(source="olx", external_id="3", url="u", title="t", city="Kraków")
        self.assertTrue(in_area(user, wieliczka))
        self.assertAlmostEqual(distance_km(user, wieliczka), 11.6, delta=1.5)
        self.assertFalse(in_area(user, katowice))  # ~70 km
        self.assertTrue(in_area(user, no_coords))  # same city name
        self.assertEqual(area_label(user), "Kraków +30 km")
        everywhere = User(id=2, telegram_id=2)
        self.assertTrue(in_area(everywhere, katowice))
        self.assertIsNone(area_label(everywhere))

    async def test_statistics_nationwide_links_local(self):
        from app.analytics import Analytics
        from app.controller import Controller
        from tests.unit.test_controller_jobs import container

        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "pro", 60)
        d = draft(name="iPhone 15 Pro", keywords=["iphone 15 pro"])
        d.deal_min_discount = 15
        search = await app.searches.create(user, d)
        now = app.clock()
        krk, waw = dict(latitude=50.06, longitude=19.94), dict(latitude=52.23, longitude=21.01)
        for i, p in enumerate([3900, 3800, 4000, 3850, 3950, 3900]):  # the market: Warsaw (far away)
            app.mock.publish(phone(f"a{i}", p, posted=now - timedelta(days=2), city="Warszawa", **waw))
        app.mock.publish(phone("near", 3200, posted=now - timedelta(hours=1), city="Kraków", **krk))
        app.mock.publish(phone("far", 3100, posted=now - timedelta(hours=1), city="Warszawa", **waw))
        ctl = Controller(container(app))
        reply = await ctl.set_area(user, "krakow", 30)
        self.assertIn("Kraków +30 km", reply.text)
        user = await app.repo.get_user(user.id)
        out = await app.monitoring.check_search(search.id)
        self.assertEqual((out.notifications, out.suppressed_area), (1, 1))
        await app.notifications.deliver_batch()
        sent = app.notifier.sent[0]
        self.assertTrue(sent.listing.external_id.endswith("near"))
        text, _ = await Analytics(container(app)).stats(user, await app.repo.get_search(search.id))
        text = text.replace("\xa0", " ")
        self.assertIn("mediana <b>3 900 PLN</b>", text)  # Warsaw prices still make the median
        self.assertIn("test-listing/near", text)
        self.assertNotIn("test-listing/far", text)
        self.assertIn("📍 Kraków 0 km", text)
        deals_text, _ = await Analytics(container(app)).deals(user, [await app.repo.get_search(search.id)])
        self.assertIn("test-listing/near", deals_text)
        self.assertNotIn("test-listing/far", deals_text)
        reply = await ctl.set_area(user, None, None)
        self.assertIn("cała Polska", reply.text)
        with self.assertRaises(ValueError):
            await app.users.set_deal_area(user, "atlantyda", 30)
