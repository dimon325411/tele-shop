"""Seller account age — a simple anti-scam signal.

Scammers on classifieds typically use freshly created accounts. A seller is "new" when the account
was created in the current calendar month (local time) or less than ``NEW_ACCOUNT_DAYS`` ago.
Only the account creation date is stored, never the seller's name.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NEW_ACCOUNT_DAYS = 30


def is_new_account(created: datetime | None, now_utc: datetime, tz: str = "Europe/Warsaw") -> bool:
    if created is None:
        return False
    if now_utc - created < timedelta(days=NEW_ACCOUNT_DAYS):
        return True
    zone = ZoneInfo(tz)
    local_created, local_now = created.astimezone(zone), now_utc.astimezone(zone)
    return (local_created.year, local_created.month) == (local_now.year, local_now.month)
