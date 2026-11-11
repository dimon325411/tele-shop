"""The same business scenarios as the unit tests, but on PostgreSQL (SqlRepository) and Redis.

Run inside docker:  docker compose --profile tests run --rm tests
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import text

import pytest

from app.domain.models import Listing, NotificationStatus
from app.domain.plans import AccessDenied
from app.services.payments import SuccessfulPaymentData
from app.storage.errors import IntegrityError
from tests.unit.helpers import App, draft

pytestmark = pytest.mark.integration


def listing(ext, title, price="100"):
    return Listing(source="mock", external_id=ext, url=f"https://example.invalid/test-listing/{ext}",
                   title=f"[TEST] {title}", price=Decimal(price) if price else None, is_test=True)


async def make_app(repo, **kw) -> App:
    return await App(repo=repo, **kw).setup()


async def test_free_notification_and_job_state_survive_repository_recreation(sql_repo):
    from app.storage.sql import SqlRepository
    app = await make_app(sql_repo)
    user = await app.user(111)
    saved = (await sql_repo.upsert_listing(listing('free', 'Oddam pralkę', '0'), app.clock())).listing
    payload = {'median': '460', 'p25': '420', 'p75': '480', 'count': 6}
    assert await sql_repo.enqueue_notification(user.id, saved.id, None, 'free', 'free:test',
                                              saved.price, app.clock(), payload=payload)
    await sql_repo.save_job_state('digest:test', {'day': '2026-11-11'}, app.clock())
    restarted = SqlRepository(sql_repo.engine)
    assert await restarted.get_job_state('digest:test') == {'day': '2026-11-11'}
    notifications = await restarted.claim_notifications(app.clock(), 10)
    assert len(notifications) == 1
    assert notifications[0].kind.value == 'free'
    assert notifications[0].payload == payload
    assert not await restarted.enqueue_notification(user.id, saved.id, None, 'free', 'free:test',
                                                    saved.price, app.clock(), payload=payload)


async def test_price_history_keeps_baseline_and_changes(sql_repo):
    from app.storage.sql import SqlRepository
    now = (await make_app(sql_repo)).clock()
    await sql_repo.upsert_listing(listing('history', 'Price history', '100'), now)
    await sql_repo.upsert_listing(listing('history', 'Price history', '100'), now + timedelta(minutes=1))
    await sql_repo.upsert_listing(listing('history', 'Price history', '80'), now + timedelta(minutes=2))
    restarted = SqlRepository(sql_repo.engine)
    async with restarted.engine.connect() as conn:
        rows = (await conn.execute(text("SELECT price, kind FROM listing_price_history ORDER BY id"))).all()
    assert rows == [(Decimal('100'), 'initial'), (Decimal('80'), 'change')]


async def test_concurrent_equal_price_upserts_write_one_change(sql_repo):
    now = (await make_app(sql_repo)).clock()
    await sql_repo.upsert_listing(listing('parallel-price', 'Parallel price', '100'), now)
    await asyncio.gather(*(sql_repo.upsert_listing(
        listing('parallel-price', 'Parallel price', '80'), now + timedelta(minutes=1))
        for _ in range(12)))
    async with sql_repo.engine.connect() as conn:
        prices = (await conn.execute(text("""
            SELECT price FROM listing_price_history
            WHERE external_id = 'parallel-price' ORDER BY id
        """))).scalars().all()
    assert prices == [Decimal('100'), Decimal('80')]


async def test_analytics_export_reads_snapshots_and_price_changes(sql_repo, tmp_path):
    import csv
    from scripts.export_analytics import export
    app = await make_app(sql_repo)
    user = await app.user(222)
    search = await app.searches.create(user, draft(keywords=['rower']))
    await app.monitoring.check_search(search.id)
    app.mock.publish(listing('report', 'Rower test', '100'))
    await app.monitoring.check_search(search.id)
    app.mock.set_price('report', Decimal('80'))
    await app.monitoring.check_search(search.id)
    async with sql_repo.engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO market_snapshots (search_id, day, variant, count, median, p25, p75, min_price)
            VALUES (:id, DATE '2020-01-01', 'all', 3, 100, 90, 110, 80)
        """), {"id": search.id})
    url = str(sql_repo.engine.url)
    counts = export(tmp_path, url)
    assert counts['snapshots'] >= 1
    with (tmp_path / 'snapshots.csv').open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    assert any(r['day'] == '2020-01-01' and r['search_id'] == str(search.id)
               and r['listing_count'] == '3' and r['median'] == '100.00' for r in rows)
    assert counts['price_changes'] == 1
    assert 'price_drops' in (tmp_path / 'price_changes.csv').read_text()


async def test_full_cycle_no_duplicates(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(111)
    search = await app.searches.create(user, draft(keywords=["rower"]))
    first = await app.monitoring.check_search(search.id)
    assert first.status == "ok" and first.baseline and first.notifications == 0
    app.mock.publish(listing("n1", "Rower szosowy"))
    second = await app.monitoring.check_search(search.id)
    assert (second.new_listings, second.matched, second.notifications) == (1, 1, 1)
    report = await app.notifications.deliver_batch()
    assert report.sent == 1
    assert app.notifier.sent[0].listing.url.endswith("/n1")
    third = await app.monitoring.check_search(search.id)
    assert third.notifications == 0
    assert (await app.notifications.deliver_batch()).sent == 0
    stored = await sql_repo.get_search(search.id)
    assert stored.baseline_done and stored.last_success_at is not None


async def test_one_notification_for_two_searches_and_parallel_delivery(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(111)
    s1 = await app.searches.create(user, draft(name="A", keywords=["rower"]))
    s2 = await app.searches.create(user, draft(name="B", keywords=["giant"]))
    for s in (s1, s2):
        await app.monitoring.check_search(s.id)
    for i in range(6):
        app.mock.publish(listing(f"g{i}", f"Rower Giant {i}"))
    results = await asyncio.gather(app.monitoring.check_search(s1.id), app.monitoring.check_search(s2.id))
    assert sum(r.notifications for r in results) == 6
    r1, r2 = await asyncio.gather(app.notifications.deliver_batch(), app.notifications.deliver_batch())
    assert r1.sent + r2.sent == 6
    assert len({n.listing.external_id for n in app.notifier.sent}) == 6 == len(app.notifier.sent)


async def test_stale_claim_recovered(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(1)
    s = await app.searches.create(user, draft())
    await app.monitoring.check_search(s.id)
    app.mock.publish(listing("k", "Rower K"))
    await app.monitoring.check_search(s.id)
    assert len(await sql_repo.claim_notifications(app.clock(), 10)) == 1
    assert (await app.notifications.deliver_batch()).sent == 0
    app.clock.advance(minutes=6)
    assert (await app.notifications.deliver_batch()).sent == 1


async def test_price_drop_and_upsert(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(1)
    await app.admin.grant(999, 1, "pro", 30)
    s = await app.searches.create(user, draft())
    await app.monitoring.check_search(s.id)
    app.mock.publish(listing("pd", "Rower PD", "1000"))
    await app.monitoring.check_search(s.id)
    app.mock.set_price("pd", Decimal("700"))
    out = await app.monitoring.check_search(s.id)
    assert out.notifications == 1
    await app.notifications.deliver_batch()
    kinds = [(n.kind, n.old_price) for n in app.notifier.sent]
    assert kinds == [("new", None), ("price_drop", Decimal("1000.00"))]


async def test_subscriptions_and_limits(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(1)
    sub = await sql_repo.get_active_subscription(user.id)
    assert sub.origin == "trial"
    with pytest.raises(IntegrityError):
        await sql_repo.create_subscription(user.id, sub.plan_id, "admin", app.clock(),
                                           app.clock() + timedelta(days=1), None)
    await app.searches.create(user, draft(name="1"))
    await app.searches.create(user, draft(name="2"))
    with pytest.raises(AccessDenied):
        await app.searches.create(user, draft(name="3"))
    app.clock.advance(days=3, seconds=1)
    assert len(await app.subs.expire_due()) == 1
    s = (await sql_repo.list_user_searches(user.id))[0]
    assert (await app.monitoring.check_search(s.id)).reason == "no_access"
    assert len(await app.subs.pop_expiry_notices()) == 1


async def test_user_isolation(sql_repo):
    app = await make_app(sql_repo)
    ala, bob = await app.user(1, "Ala"), await app.user(2, "Bob")
    s = await app.searches.create(ala, draft())
    with pytest.raises(AccessDenied):
        await app.searches.delete(bob, s.id)
    assert await sql_repo.get_search(s.id) is not None


async def test_payments_idempotent_and_refund(sql_repo):
    from app.config import PaymentsMode, Settings

    app = await make_app(sql_repo, settings=Settings(admin_ids=[999], payments_mode=PaymentsMode.TEST,
                                                     enabled_sources=["mock"]))
    user = await app.user(111)
    await app.users.accept_terms(user)
    user = await sql_repo.get_user(user.id)
    inv = await app.payments.create_invoice(user, "basic")
    assert await app.payments.pre_checkout(from_user_id=111, currency="XTR", total_amount=inv.amount,
                                           invoice_payload=inv.payload) is None
    data = SuccessfulPaymentData(from_user_id=111, currency="XTR", total_amount=inv.amount,
                                 invoice_payload=inv.payload, telegram_payment_charge_id="charge-1")
    results = await asyncio.gather(*(app.payments.handle_successful_payment(data) for _ in range(4)))
    assert sorted(r.status for r in results) == ["duplicate"] * 3 + ["granted"]
    sub = await sql_repo.get_active_subscription(user.id)
    assert (await sql_repo.get_plan(sub.plan_id)).code == "basic"

    async def refund_call(uid, charge):
        assert (uid, charge) == (111, "charge-1")

    assert await app.payments.refund("charge-1", 999, "test", refund_call) == "refunded"
    assert await sql_repo.get_active_subscription(user.id) is None
    assert (await sql_repo.get_payment_by_charge_id("charge-1")).status == "refunded"


async def test_admin_audit_stats_retention(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(1)
    await app.admin.set_plan_field(999, "basic", "max_active_searches", "8")
    assert (await sql_repo.get_plan_by_code("basic")).max_active_searches == 8
    await app.admin.set_source_enabled(999, "mock", False, "x")
    assert (await sql_repo.get_source_state("mock")).enabled is False
    log = await sql_repo.list_audit()
    assert [r["action"] for r in log] == ["source_disable", "plan_set"]
    stats = await sql_repo.stats(app.clock())
    assert stats["users"] == 1 and stats["active_subscriptions"] == 1
    await app.admin.set_source_enabled(999, "mock", True)
    s = await app.searches.create(user, draft())
    await app.monitoring.check_search(s.id)
    purged = await sql_repo.purge_old_data(app.clock() + timedelta(days=1), app.clock() + timedelta(days=1),
                                           app.clock() + timedelta(days=1))
    assert purged["listings"] > 0 and purged["runs"] == 1
    await app.users.delete_account(user)
    assert (await sql_repo.get_user(user.id)).deleted_at is not None
    assert await sql_repo.list_user_searches(user.id) == []


async def test_notification_status_values(sql_repo):
    app = await make_app(sql_repo)
    user = await app.user(1)
    s = await app.searches.create(user, draft())
    await app.monitoring.check_search(s.id)
    app.mock.publish(listing("f", "Rower F"))
    await app.monitoring.check_search(s.id)
    app.notifier.fail_with = ConnectionError("x")
    report = await app.notifications.deliver_batch()
    assert report.retried == 1
    [n] = await sql_repo.claim_notifications(app.clock() + timedelta(hours=2), 10)
    assert n.status is NotificationStatus.SENDING and n.attempts == 1


async def test_redis_locker(redis_client):
    from app.services.locks import RedisLocker

    locker = RedisLocker(redis_client)
    async with locker.hold("search:1", 30) as first:
        assert first
        async with locker.hold("search:1", 30) as second:
            assert not second
    async with locker.hold("search:1", 30) as again:
        assert again
