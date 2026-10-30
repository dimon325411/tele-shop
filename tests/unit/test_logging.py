import logging
import unittest

from app.logging_setup import SecretFilter, mask_secrets


class LoggingTests(unittest.TestCase):
    def test_mask(self):
        token = "1234567890:AAH-abcdefghijklmnopqrstuvwxyz_0123"
        self.assertEqual(mask_secrets(f"url https://api.telegram.org/bot{token}/getMe"),
                         "url https://api.telegram.org/bot<bot-token>/getMe")
        self.assertEqual(mask_secrets("postgresql+psycopg://market:s3cr3t@db:5432/market"),
                         "postgresql+psycopg://market:***@db:5432/market")
        self.assertEqual(mask_secrets("nothing secret 12:34"), "nothing secret 12:34")

    def test_filter_rewrites_record(self):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "token %s", ("1234567890:" + "A" * 35,), None)
        SecretFilter().filter(record)
        self.assertEqual(record.getMessage(), "token <bot-token>")


if __name__ == "__main__":
    unittest.main()
