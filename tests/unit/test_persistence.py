import tempfile
import unittest
from pathlib import Path

from app.storage.memory import MemoryRepository
from tests.unit.helpers import App, draft


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dump_and_load_roundtrip(self):
        app = await App().setup()
        user = await app.user(111)
        search = await app.searches.create(user, draft())
        await app.monitoring.check_search(search.id)
        await app.monitoring.market.take_snapshot(search.id)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.pickle"
            app.repo.dump(path)
            restored = MemoryRepository.load(path)
        self.assertEqual(restored.searches.keys(), app.repo.searches.keys())
        self.assertEqual(len(restored.listings), len(app.repo.listings))
        self.assertEqual(restored.matches.keys(), app.repo.matches.keys())
        self.assertEqual(restored.snapshots, app.repo.snapshots)
        # ids continue after restore (no collisions)
        user2 = await restored.create_user(222, "b", "B", "pl", "Europe/Warsaw", app.clock())
        self.assertGreater(user2.id, user.id)
        self.assertIsNotNone(await restored.get_active_subscription(user.id))

    def test_missing_file_gives_empty_repo(self):
        self.assertEqual(MemoryRepository.load("/nonexistent/state.pickle").searches, {})
