import io
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.storage.memory import MemoryRepository
from scripts.pickle_to_sql import cell, write_sql


class CellTests(unittest.TestCase):
    def test_copy_escaping(self):
        self.assertEqual(cell(None), r"\N")
        self.assertEqual(cell(True), "t")
        self.assertEqual(cell("a\tb\nc\\d"), "a\\tb\\nc\\\\d")
        self.assertEqual(cell(["olx", 'x"y']), '{"olx","x\\\\"y"}')
        self.assertEqual(cell(Decimal("2199.50")), "2199.50")
        self.assertEqual(cell(datetime(2026, 11, 8, tzinfo=timezone.utc)), "2026-11-08T00:00:00+00:00")


class ExportTests(unittest.TestCase):
    def test_empty_state_is_one_guarded_transaction(self):
        out = io.StringIO()
        counts = write_sql(MemoryRepository(), out)
        sql = out.getvalue()
        self.assertTrue(sql.startswith("-- generated"))
        self.assertIn("refusing to import", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertEqual(counts["listings"], 0)
