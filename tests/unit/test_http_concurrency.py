import asyncio
import unittest

from app.sources.http import HttpResponse, PoliteFetcher
from app.sources.base import SourceBlocked


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_request_observes_first_requests_429(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def transport(url, headers, timeout):
            calls.append(url)
            if len(calls) == 1:
                started.set()
                await release.wait()
                return HttpResponse(429, 'limited', {'retry-after': '3600'})
            return HttpResponse(200, 'ok', {})

        f = PoliteFetcher(transport, min_interval=0)
        first = asyncio.create_task(f.get('https://example.invalid/a'))
        await started.wait()
        second = asyncio.create_task(f.get('https://example.invalid/b'))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(all(isinstance(x, SourceBlocked) for x in results))
        self.assertEqual(len(calls), 1)
