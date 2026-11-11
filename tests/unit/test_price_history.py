from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.models import Listing
from app.storage.memory import MemoryRepository
from scripts.pickle_to_sql import rows_for


@pytest.mark.asyncio
async def test_price_history_records_initial_price_and_each_change_once():
    repo = MemoryRepository()
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)

    def listing(price):
        return Listing(source="mock", external_id="one", url="https://example.invalid/one",
                       title="One", price=Decimal(price) if price is not None else None)

    await repo.upsert_listing(listing("100"), now)
    await repo.upsert_listing(listing("100"), now + timedelta(minutes=1))
    await repo.upsert_listing(listing(None), now + timedelta(minutes=2))
    await repo.upsert_listing(listing("80"), now + timedelta(minutes=3))
    await repo.upsert_listing(listing("120"), now + timedelta(minutes=4))

    assert [r["price"] for r in repo.price_history] == [Decimal("100"), Decimal("80"), Decimal("120")]
    assert [r["kind"] for r in repo.price_history] == ["initial", "change", "change"]
    assert rows_for(repo)["listing_price_history"] == repo.price_history


@pytest.mark.asyncio
async def test_price_history_survives_file_roundtrip(tmp_path):
    repo = MemoryRepository()
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    await repo.upsert_listing(Listing(source="mock", external_id="one", url="https://example.invalid/one",
                                      title="One", price=Decimal("100")), now)
    path = tmp_path / "state.pickle"
    repo.dump(path)
    assert MemoryRepository.load(path).price_history == repo.price_history


@pytest.mark.asyncio
async def test_export_adds_baseline_for_legacy_listings_without_history():
    repo = MemoryRepository()
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    for ext in ("old", "new"):
        await repo.upsert_listing(Listing(source="mock", external_id=ext, url=f"https://example.invalid/{ext}",
                                          title=ext, price=Decimal("100")), now)
    repo.price_history = [repo.price_history[-1]]  # mixed old and new state
    rows = rows_for(repo)["listing_price_history"]
    assert {(r["external_id"], r["kind"]) for r in rows} == {("old", "baseline"), ("new", "initial")}
