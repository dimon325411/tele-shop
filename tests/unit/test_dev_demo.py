"""The demo scheduler loop (without Telegram) produces a notification for a growing mock catalogue."""

import asyncio
import unittest

from app.sources.mock import MockSource
from app.worker import jobs
from tests.unit.helpers import App, draft
from tests.unit.test_controller_jobs import container


class DemoFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_check_is_not_queued_again_and_is_cancelled_on_shutdown(self):
        from unittest.mock import patch
        from app.dev_run import scheduler_loop

        app = await App().setup()
        started, ended = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow_check(c, sid):
            calls.append(sid)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()

        ticks = 0
        async def tick(c, enqueue):
            nonlocal ticks
            await enqueue(1)
            ticks += 1

        with patch('app.dev_run.jobs.schedule_tick', tick), patch('app.dev_run.jobs.run_check', slow_check):
            task = asyncio.create_task(scheduler_loop(container(app), .001))
            await asyncio.wait_for(started.wait(), 1)
            while ticks < 3:
                await asyncio.sleep(.001)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertEqual(calls, [1])
        self.assertTrue(ended.is_set())

    async def test_growing_mock_source_triggers_notification(self):
        app = await App(mock=MockSource(grow=True)).setup()
        await app.repo.update_plan("trial", min_interval_seconds=60)
        c = container(app)
        user = await app.user(1)
        await app.searches.create(user, draft(keywords=["rower"]))
        tasks = []

        async def enqueue(sid):
            tasks.append(asyncio.create_task(jobs.run_check(c, sid)))

        for _ in range(3):  # three ticks, one minute apart
            await jobs.schedule_tick(c, enqueue)
            await asyncio.gather(*tasks)
            tasks.clear()
            await jobs.deliver_notifications(c)
            app.clock.advance(seconds=61)
        titles = [n.listing.title for n in app.notifier.sent]
        self.assertEqual(titles, ["[TEST] Rower szosowy Giant Contend"])  # 2nd tick adds it; 3rd adds a non-match
        self.assertTrue(app.notifier.texts)  # baseline summary was sent


if __name__ == "__main__":
    unittest.main()
