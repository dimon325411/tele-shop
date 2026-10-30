"""End-to-end pipeline on test data: search -> fetch -> match -> store -> notify -> no duplicates."""

import asyncio
import unittest
from datetime import timedelta
from decimal import Decimal

from app.domain.models import Listing, NotificationStatus
from app.domain.plans import AccessDenied
from app.services.notifications import NotifierBlocked, NotifierRetryLater
from app.sources.base import SourceAdapter, SourceBlocked, SourceCapabilities
from app.sources.mock import MockSource
from tests.unit.helpers import App, draft


def listing(ext, title, price="100", **kw):
    return Listing(source="mock", external_id=ext, url=f"https://example.invalid/test-listing/{ext}",
                   title=f"[TEST] {title}", price=Decimal(price) if price else None, **kw)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.user = await self.app.user(111)

    async def test_full_cycle_and_no_duplicates(self):
        app = self.app
        search = await app.searches.create(self.user, draft(keywords=["rower"]))
        # first run: baseline (existing listings are remembered, not sent)
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.status, "ok")
        self.assertTrue(out.baseline)
        self.assertGreaterEqual(out.matched, 2)  # "Rower górski", "Rowerek biegowy"
        self.assertEqual(out.notifications, 0)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 0)

        # a new matching listing appears
        app.mock.publish(listing("n1", "Rower szosowy Giant", "2400", city="Kraków", condition="used"))
        app.mock.publish(listing("n2", "Sofa narożna"))  # not matching
        out = await app.monitoring.check_search(search.id)
        self.assertEqual((out.new_listings, out.matched, out.notifications), (2, 1, 1))
        report = await app.notifications.deliver_batch()
        self.assertEqual(report.sent, 1)
        sent = app.notifier.sent[0]
        self.assertEqual(sent.user.telegram_id, 111)
        self.assertEqual(sent.listing.url, "https://example.invalid/test-listing/n1")
        self.assertEqual(sent.search_name, "Rowery")
        self.assertTrue(sent.listing.is_test)

        # re-running finds nothing new and sends nothing
        out = await app.monitoring.check_search(search.id)
        self.assertEqual((out.new_listings, out.matched, out.notifications), (0, 0, 0))
        self.assertEqual((await app.notifications.deliver_batch()).sent, 0)
        self.assertEqual(len(app.notifier.sent), 1)
        stored = await app.repo.get_search(search.id)
        self.assertIsNotNone(stored.last_success_at)
        self.assertTrue(stored.baseline_done)

    async def test_notify_on_first_run_option(self):
        app = self.app
        app.settings.notify_on_first_run = True
        search = await app.searches.create(self.user, draft(keywords=["iphone"]))
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.notifications, out.matched)
        self.assertGreater(out.matched, 0)

    async def test_one_notification_for_several_searches(self):
        app = self.app
        s1 = await app.searches.create(self.user, draft(name="A", keywords=["rower"]))
        s2 = await app.searches.create(self.user, draft(name="B", keywords=["giant"]))
        for s in (s1, s2):
            await app.monitoring.check_search(s.id)
        app.mock.publish(listing("x", "Rower Giant Contend"))
        o1 = await app.monitoring.check_search(s1.id)
        o2 = await app.monitoring.check_search(s2.id)
        self.assertEqual((o1.matched, o2.matched), (1, 1))  # both searches record the match
        self.assertEqual(o1.notifications + o2.notifications, 1)  # but the user gets one message
        await app.notifications.deliver_batch()
        self.assertEqual(len(app.notifier.sent), 1)

    async def test_users_are_isolated(self):
        app = self.app
        bob = await app.user(222, "Bob")
        sa = await app.searches.create(self.user, draft(keywords=["rower"]))
        sb = await app.searches.create(bob, draft(keywords=["sofa"]))
        for s in (sa, sb):
            await app.monitoring.check_search(s.id)
        app.mock.publish(listing("r", "Rower BMX"))
        app.mock.publish(listing("s", "Sofa skórzana"))
        for s in (sa, sb):
            await app.monitoring.check_search(s.id)
        await app.notifications.deliver_batch()
        got = {(n.user.telegram_id, n.listing.external_id) for n in app.notifier.sent}
        self.assertEqual(got, {(111, "r"), (222, "s")})
        # Bob cannot touch Ala's search
        with self.assertRaises(AccessDenied):
            await app.searches.pause(bob, sa.id)
        with self.assertRaises(AccessDenied):
            await app.searches.delete(bob, sa.id)
        self.assertTrue((await app.repo.get_search(sa.id)).is_active)

    async def test_parallel_checks_do_not_duplicate(self):
        app = self.app
        search = await app.searches.create(self.user, draft(keywords=["rower"]))
        await app.monitoring.check_search(search.id)
        for i in range(5):
            app.mock.publish(listing(f"p{i}", f"Rower nr {i}"))
        outcomes = await asyncio.gather(*(app.monitoring.check_search(search.id) for _ in range(8)))
        statuses = sorted(o.status for o in outcomes)
        self.assertIn("ok", statuses)
        self.assertEqual(sum(o.notifications for o in outcomes), 5)
        # two delivery workers at the same time
        r1, r2 = await asyncio.gather(app.notifications.deliver_batch(), app.notifications.deliver_batch())
        self.assertEqual(r1.sent + r2.sent, 5)
        self.assertEqual(len({n.listing.external_id for n in app.notifier.sent}), 5)
        self.assertEqual(len(app.notifier.sent), 5)

    async def test_parallel_checks_without_lock_still_deduplicate(self):
        """Even if the lock is lost (e.g. TTL expiry), DB uniqueness prevents duplicates."""
        from contextlib import asynccontextmanager

        class NoLock:
            @asynccontextmanager
            async def hold(self, key, ttl):
                yield True

        app = self.app
        app.monitoring.locker = NoLock()
        search = await app.searches.create(self.user, draft(keywords=["rower"]))
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("z", "Rower Z"))
        outcomes = await asyncio.gather(*(app.monitoring.check_search(search.id) for _ in range(6)))
        self.assertEqual(sum(o.notifications for o in outcomes), 1)

    async def test_retry_with_backoff_and_recovery(self):
        app = App(mock=MockSource(grow=False, fail_times=2))
        await app.setup()
        user = await app.user(1)
        search = await app.searches.create(user, draft())
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.status, "failed")
        s = await app.repo.get_search(search.id)
        self.assertEqual(s.consecutive_failures, 1)
        self.assertGreaterEqual((s.next_check_at - app.clock()).total_seconds(), s.interval_seconds)
        out2 = await app.monitoring.check_search(search.id)
        self.assertEqual(out2.status, "failed")
        self.assertEqual((await app.repo.get_search(search.id)).consecutive_failures, 2)
        out3 = await app.monitoring.check_search(search.id)
        self.assertEqual(out3.status, "ok")
        s = await app.repo.get_search(search.id)
        self.assertEqual(s.consecutive_failures, 0)
        self.assertEqual(app.repo.runs[0]["status"], "error")
        state = await app.repo.get_source_state("mock")
        self.assertEqual(state.status, "ok")

    async def test_blocked_source_pauses_without_bypass(self):
        class Blocking(SourceAdapter):
            code = "mock"
            is_test_source = True
            calls = 0

            def capabilities(self):
                return SourceCapabilities()

            async def fetch(self, criteria, limit=50):
                Blocking.calls += 1
                raise SourceBlocked("429 Too Many Requests", retry_after_seconds=600)

        app = await App(mock=Blocking()).setup()
        user = await app.user(1)
        search = await app.searches.create(user, draft())
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.source_status["mock"], "blocked")
        state = await app.repo.get_source_state("mock")
        self.assertEqual(state.status, "blocked")
        self.assertEqual(state.blocked_until, app.clock() + timedelta(seconds=600))
        # while in cool-down the source is not called at all
        await app.monitoring.check_search(search.id)
        self.assertEqual(Blocking.calls, 1)
        app.clock.advance(seconds=601)
        await app.monitoring.check_search(search.id)
        self.assertEqual(Blocking.calls, 2)

    async def test_disabled_real_sources_never_fetch(self):
        app = await App(enabled=["mock", "olx"]).setup()
        user = await app.user(1)
        search = await app.searches.create(user, draft(sources=["olx"]))
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.source_status, {"olx": "disabled"})
        self.assertEqual(out.status, "skipped")
        self.assertEqual(app.repo.runs[-1]["status"], "disabled")
        self.assertIn("2.3", app.repo.runs[-1]["error"])
        # a source not enabled in configuration cannot even be selected
        with self.assertRaises(AccessDenied) as ctx:
            await self.app.searches.create(self.user, draft(sources=["allegro_lokalnie"]))
        self.assertEqual(ctx.exception.code, "source_disabled")

    async def test_admin_can_switch_off_source(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.admin.set_source_enabled(999, "mock", False, "maintenance")
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.source_status["mock"], "disabled")
        self.assertEqual(app.mock.calls, 0)
        await app.admin.set_source_enabled(999, "mock", True)
        await app.monitoring.check_search(search.id)
        self.assertEqual(app.mock.calls, 1)
        self.assertEqual([a["action"] for a in app.repo.audit], ["source_disable", "source_enable"])

    async def test_restart_recovers_stuck_notifications(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("k", "Rower K"))
        await app.monitoring.check_search(search.id)
        claimed = await app.repo.claim_notifications(app.clock(), 10)  # worker claims then "crashes"
        self.assertEqual(len(claimed), 1)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 0)  # still owned by the dead worker
        app.clock.advance(minutes=6)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 1)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 0)

    async def test_due_searches_survive_restart(self):
        app = self.app
        s = await app.searches.create(self.user, draft())
        self.assertEqual(await app.monitoring.plan_due_checks(), [s.id])
        self.assertEqual(await app.monitoring.plan_due_checks(), [])  # slot reserved
        app.clock.advance(seconds=s.interval_seconds + 1)  # job lost in a crash -> picked up again
        self.assertEqual(await app.monitoring.plan_due_checks(), [s.id])

    async def test_fair_scheduling_between_users(self):
        app = self.app
        app.settings.max_checks_per_user_per_tick = 2
        bob = await app.user(222, "Bob")
        await app.admin.grant(999, 111, "premium", 30)
        for i in range(10):
            await app.searches.create(self.user, draft(name=f"a{i}"))
        sb = await app.searches.create(bob, draft(name="b"))
        picked = await app.monitoring.plan_due_checks()
        self.assertIn(sb.id, picked)
        self.assertEqual(len(picked), 3)

    async def test_notification_failures(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("f", "Rower F"))
        await app.monitoring.check_search(search.id)
        app.notifier.fail_with = NotifierRetryLater(30)
        self.assertEqual((await app.notifications.deliver_batch()).retried, 1)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 0)  # waits 30 s
        app.clock.advance(seconds=31)
        app.notifier.fail_with = ConnectionError("network")
        for _ in range(4):
            await app.notifications.deliver_batch()
            app.clock.advance(hours=1)
        report = await app.notifications.deliver_batch()
        self.assertEqual(report.failed, 1)
        n = next(iter(app.repo.notifications.values()))
        self.assertEqual((n.status, n.attempts), (NotificationStatus.FAILED, 5))

    async def test_user_blocked_bot(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("b", "Rower B"))
        await app.monitoring.check_search(search.id)
        app.notifier.fail_with = NotifierBlocked()
        self.assertEqual((await app.notifications.deliver_batch()).cancelled, 1)
        self.assertTrue((await app.repo.get_user(self.user.id)).bot_blocked)

    async def test_quiet_hours_defer(self):
        app = self.app
        await app.users.set_quiet_hours(self.user, 11, 13)  # it is 12:00 local
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("q", "Rower Q"))
        await app.monitoring.check_search(search.id)
        self.assertEqual((await app.notifications.deliver_batch()).deferred, 1)
        app.clock.advance(minutes=30)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 0)
        app.clock.advance(minutes=31)  # 13:01 local
        self.assertEqual((await app.notifications.deliver_batch()).sent, 1)

    async def test_daily_limit(self):
        app = self.app
        await app.repo.update_plan("trial", max_notifications_per_day=2)
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        for i in range(3):
            app.mock.publish(listing(f"d{i}", f"Rower {i}"))
        await app.monitoring.check_search(search.id)
        report = await app.notifications.deliver_batch()
        self.assertEqual((report.sent, report.limited), (2, 1))

    async def test_price_drop_alert(self):
        app = self.app
        await app.admin.grant(999, 111, "pro", 30)
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("pd", "Rower PD", "1000"))
        await app.monitoring.check_search(search.id)
        app.mock.set_price("pd", Decimal("980"))  # 2 % - too small
        await app.monitoring.check_search(search.id)
        app.mock.set_price("pd", Decimal("800"))
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.notifications, 1)
        await app.notifications.deliver_batch()
        kinds = [(n.kind, n.old_price) for n in app.notifier.sent]
        self.assertEqual(kinds, [("new", None), ("price_drop", Decimal("980"))])
        # same price again -> no repeated alert
        out = await app.monitoring.check_search(search.id)
        self.assertEqual(out.notifications, 0)

    async def test_price_drop_needs_plan_feature(self):
        app = self.app  # trial plan has no price-drop alerts
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("pd", "Rower PD", "1000"))
        await app.monitoring.check_search(search.id)
        app.mock.set_price("pd", Decimal("500"))
        self.assertEqual((await app.monitoring.check_search(search.id)).notifications, 0)


class SubscriptionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.user = await self.app.user(111)

    async def test_trial_given_once(self):
        app = self.app
        sub = await app.repo.get_active_subscription(self.user.id)
        self.assertEqual(sub.origin, "trial")
        with self.assertRaises(AccessDenied) as ctx:
            await app.subs.start_trial(await app.repo.get_user(self.user.id))
        self.assertEqual(ctx.exception.code, "trial_used")
        _, created = await app.users.register(111, "ala", "Ala")
        self.assertFalse(created)
        self.assertEqual(len(app.repo.subscriptions), 1)

    async def test_plan_limits_enforced_on_server(self):
        app = self.app  # trial: 2 searches
        await app.searches.create(self.user, draft(name="1"))
        s2 = await app.searches.create(self.user, draft(name="2"))
        with self.assertRaises(AccessDenied) as ctx:
            await app.searches.create(self.user, draft(name="3"))
        self.assertEqual(ctx.exception.code, "search_limit")
        await app.searches.pause(self.user, s2.id)
        s3 = await app.searches.create(self.user, draft(name="3"))
        with self.assertRaises(AccessDenied):
            await app.searches.resume(self.user, s2.id)  # would exceed the limit again
        self.assertEqual(s3.interval_seconds, 900)  # trial minimum interval
        with self.assertRaises(AccessDenied) as ctx:
            await app.searches.create(self.user, draft(name="f", fuzzy=True))
        self.assertIn(ctx.exception.code, {"search_limit", "feature_not_in_plan"})

    async def test_interval_clamped_to_plan(self):
        s = await self.app.searches.create(self.user, draft(interval_seconds=30))
        self.assertEqual(s.interval_seconds, 900)

    async def test_expiry_stops_monitoring_keeps_searches(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        app.clock.advance(days=3, seconds=1)
        expired = await app.subs.expire_due()
        self.assertEqual(len(expired), 1)
        out = await app.monitoring.check_search(search.id)
        self.assertEqual((out.status, out.reason), ("skipped", "no_access"))
        self.assertEqual(app.mock.calls, 0)
        self.assertIsNotNone(await app.repo.get_search(search.id))  # settings kept
        notices = await app.subs.pop_expiry_notices()
        self.assertEqual([s.user_id for s in notices], [self.user.id])
        self.assertEqual(await app.subs.pop_expiry_notices(), [])  # told only once
        with self.assertRaises(AccessDenied):
            await app.searches.create(self.user, draft())
        # scheduler skips users without access and does not spin on them
        self.assertEqual(await app.monitoring.plan_due_checks(), [])
        # renewal resumes monitoring of the saved search
        await app.admin.grant(999, 111, "basic", 30)
        app.clock.advance(hours=2)
        self.assertEqual(await app.monitoring.plan_due_checks(), [search.id])

    async def test_pending_notifications_cancelled_after_expiry(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(listing("e", "Rower E"))
        await app.monitoring.check_search(search.id)
        app.clock.advance(days=4)
        await app.subs.expire_due()
        self.assertEqual((await app.notifications.deliver_batch()).cancelled, 1)

    async def test_downgrade_keeps_extra_searches_unmonitored(self):
        app = self.app
        await app.admin.grant(999, 111, "basic", 30)
        ids = [(await app.searches.create(self.user, draft(name=str(i)))).id for i in range(5)]
        await app.admin.grant(999, 111, "trial", 3)  # 2 searches allowed
        monitored = await app.searches.monitored_ids(await app.repo.get_user(self.user.id))
        self.assertEqual(monitored, set(ids[:2]))
        out = await app.monitoring.check_search(ids[4])
        self.assertEqual(out.reason, "over_plan_limit")

    async def test_suspended_user(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.admin.set_suspended(999, 111, True, "spam")
        self.assertFalse((await app.repo.get_search(search.id)).is_active)
        user = await app.repo.get_user(self.user.id)
        with self.assertRaises(AccessDenied) as ctx:
            await app.searches.create(user, draft())
        self.assertEqual(ctx.exception.code, "suspended")

    async def test_account_deletion(self):
        app = self.app
        search = await app.searches.create(self.user, draft())
        await app.monitoring.check_search(search.id)
        await app.users.delete_account(self.user)
        user = await app.repo.get_user(self.user.id)
        self.assertIsNotNone(user.deleted_at)
        self.assertIsNone(user.username)
        self.assertEqual(await app.repo.list_user_searches(self.user.id), [])
        self.assertEqual(app.repo.matches, {})
        self.assertIsNone(await app.repo.get_active_subscription(self.user.id))


if __name__ == "__main__":
    unittest.main()
