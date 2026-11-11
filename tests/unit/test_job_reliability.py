import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.config import Settings
from app.storage.memory import MemoryRepository
from app.worker import jobs
from tests.unit.helpers import App, draft
from tests.unit.test_controller_jobs import container
from tests.unit import test_freebies as free_tests
from tests.unit.test_freebies import offer


class PersistentJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_seen_state_is_preserved_on_upgrade(self):
        app = await App().setup()
        app.repo.free_seen = {'123': app.clock().isoformat()}
        app.repo.digest_sent = {7: '2026-11-11'}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state'
            app.repo.dump(path)
            restored = MemoryRepository.load(path)
        self.assertEqual((await restored.get_job_state('digest:7'))['day'], '2026-11-11')
        self.assertGreater((await restored.get_job_state('free:123'))['until'], app.clock().isoformat())

    async def test_job_state_survives_file_restart(self):
        app = await App().setup()
        await app.repo.save_job_state('digest:111', {'day': '2026-11-11'}, app.clock())
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state'
            app.repo.dump(path)
            restored = MemoryRepository.load(path)
        self.assertEqual(await restored.get_job_state('digest:111'), {'day': '2026-11-11'})

    async def test_failed_digest_is_retried_and_success_is_persisted(self):
        app = await App(settings=Settings(personal_mode=True, admin_ids=[999], digest_hour=0)).setup()
        user = await app.user(999)
        await app.searches.create(user, draft())
        async def fail(*args):
            raise ConnectionError('offline')
        original = app.notifier.send_text
        app.notifier.send_text = fail
        c = container(app)
        self.assertEqual(await jobs.daily_digest(c), 0)
        app.notifier.send_text = original
        self.assertEqual(await jobs.daily_digest(c), 1)
        self.assertEqual(await jobs.daily_digest(c), 0)
        self.assertEqual((await app.repo.get_job_state(f'digest:{user.id}'))['day'],
                         app.clock().astimezone(__import__('zoneinfo').ZoneInfo(user.timezone)).date().isoformat())


class FreeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = free_tests.ScanTests.asyncSetUp
    async def prepare_delivery(self):
        app = await App(settings=Settings(personal_mode=True, admin_ids=[10]), repo=self.repo).setup()
        app.clock.now = self.clock()
        user = await app.user(10)
        await app.repo.update_user(user.id, deal_city='krakow', deal_radius_km=30, free_alerts=True)
        self.owner = await app.repo.get_user(user.id)
        self.payloads['oddam'] = [offer(1, 'Oddam pralkę Bosch Serie 4', free=True)]
        self.payloads['pralke bosch serie'] = [offer(100+i, 'Pralka Bosch Serie 4', price=p)
                                               for i, p in enumerate([400, 450, 500, 420, 480, 470])]
        return app

    async def test_free_item_retries_delivery_and_keeps_estimate_after_restart(self):
        app = await self.prepare_delivery()
        await self.service.scan([self.owner], self.notifier)
        self.assertEqual(len(self.notifier.sent), 0)  # queued, never sent directly
        app.notifier.fail_with = ConnectionError('offline')
        self.assertEqual((await app.notifications.deliver_batch()).retried, 1)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state'
            app.repo.dump(path)
            restored = MemoryRepository.load(path)
        app.notifications.repo = restored
        app.subs.repo = restored
        app.clock.advance(minutes=2)
        app.notifier.fail_with = None
        self.assertEqual((await app.notifications.deliver_batch()).sent, 1)
        sent = app.notifier.sent[-1]
        self.assertEqual(sent.kind, 'free')
        self.assertEqual(int(sent.price_stats.median), 460)

    async def test_free_items_respect_quiet_hours(self):
        app = await self.prepare_delivery()
        await app.repo.update_user(self.owner.id, quiet_start_hour=0, quiet_end_hour=23)
        await self.service.scan([self.owner], self.notifier)
        self.assertEqual((await app.notifications.deliver_batch()).deferred, 1)
        self.assertEqual(app.notifier.sent, [])

    async def test_transient_estimate_failure_is_not_marked_seen(self):
        app = await self.prepare_delivery()
        from app.sources.base import SourceUnavailable
        original = self.service.estimate
        async def fail(*args):
            raise SourceUnavailable('offline')
        self.service.estimate = fail
        await self.service.scan([self.owner], self.notifier)
        self.service.estimate = original
        await self.service.scan([self.owner], self.notifier)
        self.assertEqual(len(app.repo.notifications), 1)

    async def test_missing_valuation_can_recover_after_thirty_minutes(self):
        app = await self.prepare_delivery()
        paid = self.payloads['pralke bosch serie']
        self.payloads['pralke bosch serie'] = []
        await self.service.scan([self.owner])
        self.payloads['pralke bosch serie'] = paid
        self.clock.advance(minutes=31)
        await self.service.scan([self.owner])
        self.assertEqual(len(app.repo.notifications), 1)
