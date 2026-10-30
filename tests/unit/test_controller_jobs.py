import unittest

from app.container import Container
from app.controller import Controller
from app.services.admin import RetentionService
from app.worker import jobs
from tests.unit.helpers import App, draft


def container(app: App) -> Container:
    c = Container(settings=app.settings, repo=app.repo, registry=app.registry, locker=app.locker, subs=app.subs,
                  users=app.users, searches=app.searches, monitoring=app.monitoring, payments=app.payments,
                  admin=app.admin, retention=RetentionService(app.repo, app.settings, app.clock))
    c.notifications = app.notifications
    return c


def callbacks(reply):
    return [b.callback for row in reply.buttons for b in row if b.callback]


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.c = container(self.app)
        self.ctl = Controller(self.c)

    async def _user(self, tg=111):
        return await self.app.repo.get_user_by_telegram_id(tg)

    async def test_start_new_and_returning(self):
        r = await self.ctl.start(111, "ala", "Ala <3")
        self.assertIn("Ala &lt;3", r.text)
        self.assertIn("Free Trial", r.text)
        self.assertIn("menu:new", callbacks(r))
        r2 = await self.ctl.start(111, "ala", "Ala")
        self.assertIn("okres próbny", r2.text)  # still no searches -> full welcome again
        await self.app.searches.create(await self._user(), draft())
        r3 = await self.ctl.start(111, "ala", "Ala")
        self.assertIn("Witaj ponownie", r3.text)

    async def test_static_pages(self):
        await self.ctl.start(111, "ala", "Ala")
        user = await self._user()
        for reply in [self.ctl.help(), self.ctl.terms(user), self.ctl.privacy(), self.ctl.support(user),
                      self.ctl.paysupport(user)]:
            self.assertTrue(reply.text)
        self.assertIn("111", self.ctl.paysupport(user).text)
        self.assertIn("plan:terms", callbacks(self.ctl.terms(user)))

    async def test_search_management_flow(self):
        await self.ctl.start(111, "ala", "Ala")
        user = await self._user()
        self.assertIn("Nie masz jeszcze", (await self.ctl.list_searches(user)).text)
        s = await self.app.searches.create(user, draft(name="Rower <x>"))
        r = await self.ctl.list_searches(user)
        self.assertIn("Rower &lt;x&gt;", r.text)
        self.assertIn(f"s:pause:{s.id}", callbacks(r))
        self.assertIn(f"s:pause:{s.id}", callbacks(await self.ctl.pick(user, "pause")))
        self.assertIn("Wstrzymano", (await self.ctl.search_action(user, "pause", s.id)).text)
        self.assertIn(f"s:resume:{s.id}", callbacks(await self.ctl.pick(user, "resume")))
        self.assertIn("Wznowiono", (await self.ctl.search_action(user, "resume", s.id)).text)
        confirm = await self.ctl.search_action(user, "delete", s.id)
        self.assertIn(f"s:delconfirm:{s.id}", callbacks(confirm))
        self.assertIn("Usunięto", (await self.ctl.search_action(user, "delconfirm", s.id)).text)
        self.assertEqual(await self.app.repo.list_user_searches(user.id), [])

    async def test_other_users_search_is_not_found(self):
        await self.ctl.start(111, "ala", "Ala")
        await self.ctl.start(222, "bob", "Bob")
        ala, bob = await self._user(111), await self._user(222)
        s = await self.app.searches.create(ala, draft())
        for action in ("pause", "delete", "delconfirm"):
            self.assertIn("Nie znaleziono", (await self.ctl.search_action(bob, action, s.id)).text)

    async def test_status_and_plan(self):
        await self.ctl.start(111, "ala", "Ala")
        user = await self._user()
        r = await self.ctl.status(user)
        self.assertIn("Free Trial", r.text)
        self.assertIn("OLX.pl: wyłączone", r.text)
        self.assertIn("Allegro Lokalnie: wyłączone", r.text)
        self.assertIn("Źródło testowe: dane testowe", r.text)
        plan = await self.ctl.plan(user)
        self.assertIn("Premium", plan.text)
        self.assertIn("plan:terms", callbacks(plan))
        self.assertIn("plan:buy:pro", callbacks(plan))
        buy = await self.ctl.buy(user, "pro")
        self.assertIsNone(buy.invoice)  # terms first
        await self.ctl.accept_terms(user)
        buy = await self.ctl.buy(await self._user(), "pro")
        self.assertEqual((buy.invoice.amount, buy.invoice.currency), (350, "XTR"))

    async def test_plan_when_payments_disabled(self):
        from app.config import Settings

        app = await App(settings=Settings()).setup()
        ctl = Controller(container(app))
        await ctl.start(1, "a", "A")
        user = await app.repo.get_user_by_telegram_id(1)
        r = await ctl.plan(user)
        self.assertIn("Płatności są obecnie wyłączone", r.text)
        self.assertEqual(callbacks(r), [])

    async def test_settings(self):
        await self.ctl.start(111, "ala", "Ala")
        user = await self._user()
        reply, ok = await self.ctl.set_quiet_hours(user, "25-3")
        self.assertFalse(ok)
        reply, ok = await self.ctl.set_quiet_hours(user, "22-7")
        self.assertTrue(ok)
        user = await self._user()
        self.assertIn("22:00–7:00", self.ctl.settings_view(user).text)
        self.assertIn("settings:newsellers", callbacks(self.ctl.settings_view(user)))
        await self.ctl.toggle_new_sellers(user)
        self.assertTrue((await self._user()).hide_new_sellers)
        await self.ctl.toggle_price_drops(user)
        self.assertFalse((await self._user()).notify_price_drops)
        self.assertIn("settings:delete_confirm", callbacks(self.ctl.confirm_delete_account(user)))
        await self.ctl.delete_account(user)
        self.assertIsNotNone((await self._user()).deleted_at)

    async def test_admin_commands(self):
        await self.ctl.start(111, "ala", "Ala")
        self.assertIn("tylko dla administratora", (await self.ctl.admin(111, "admin", [])).text)
        self.assertIn("Użytkownicy: 1", (await self.ctl.admin(999, "admin", [])).text)
        self.assertIn("olx", (await self.ctl.admin(999, "admin_sources", [])).text)
        self.assertIn("Gotowe", (await self.ctl.admin(999, "source_off", ["mock", "awaria"])).text)
        self.assertIn("Gotowe", (await self.ctl.admin(999, "plan_set", ["basic", "max_active_searches", "9"])).text)
        self.assertIn("searches=9", (await self.ctl.admin(999, "plans_admin", [])).text)
        self.assertIn("Nieznane pole", (await self.ctl.admin(999, "plan_set", ["basic", "zzz", "1"])).text)
        self.assertIn("Gotowe", (await self.ctl.admin(999, "grant", ["111", "pro", "10"])).text)
        self.assertIn("Użycie", (await self.ctl.admin(999, "grant", ["111", "pro"])).text)
        self.assertIn("Gotowe", (await self.ctl.admin(999, "suspend", ["111", "spam"])).text)
        self.assertTrue((await self._user()).is_suspended)
        self.assertIn("Gotowe", (await self.ctl.admin(999, "unsuspend", ["111"])).text)
        self.assertIn("Brak wpisów", (await self.ctl.admin(999, "payerrors", [])).text)
        audit = (await self.ctl.admin(999, "audit", [])).text
        for action in ("source_disable", "plan_set", "grant", "suspend", "unsuspend"):
            self.assertIn(action, audit)

        async def refund_call(uid, charge):
            pass

        self.assertIn("Nie znaleziono", (await self.ctl.admin(999, "refund", ["nope"], refund_call)).text)


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_check_deliver_and_expire(self):
        app = await App().setup()
        c = container(app)
        user = await app.user(111)
        search = await app.searches.create(user, draft())
        queued: list[int] = []

        async def enqueue(sid):
            queued.append(sid)

        self.assertEqual(await jobs.schedule_tick(c, enqueue), 1)
        result = await jobs.run_check(c, queued[0])
        self.assertEqual(result["status"], "ok")
        self.assertIn("pierwsze sprawdzenie", app.notifier.texts[0][1])  # baseline summary
        app.mock.publish(app.mock._listings[0].__class__(
            source="mock", external_id="new", url="https://example.invalid/test-listing/new",
            title="[TEST] Rower nowy", is_test=True))
        await jobs.run_check(c, search.id)
        self.assertEqual(await jobs.deliver_notifications(c), 1)
        self.assertEqual(await jobs.deliver_notifications(c), 0)
        app.clock.advance(days=4)
        self.assertEqual(await jobs.expire_subscriptions(c), 1)
        self.assertIn("wygasła", app.notifier.texts[-1][1])
        self.assertEqual(await jobs.expire_subscriptions(c), 0)
        app.clock.advance(days=100)
        purged = await jobs.retention(c)
        self.assertGreater(purged["listings"], 0)


if __name__ == "__main__":
    unittest.main()
