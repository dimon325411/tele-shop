"""PostgreSQL repository (SQLAlchemy 2 async engine + psycopg 3 driver).

The statements live in ``app.storage.sql_queries`` and are verified against a real
PostgreSQL by ``scripts/check_sql.py`` and ``scripts/check_sql_semantics.py``.
Integration tests for this class: ``tests/integration/test_sql_repository.py``.
"""

from __future__ import annotations

import contextvars
import json
from contextlib import asynccontextmanager
from dataclasses import fields as dc_fields
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.domain.models import (
    Condition,
    Delivery,
    Listing,
    Notification,
    NotificationKind,
    NotificationStatus,
    Payment,
    Plan,
    Search,
    SearchCriteria,
    SourceState,
    StoredListing,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.storage import sql_queries as q
from app.storage.errors import IntegrityError
from app.storage.repo import UpsertResult

_CRITERIA_FIELDS = {f.name for f in dc_fields(SearchCriteria)}


def make_engine(url: str, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True, pool_size=10, max_overflow=10)


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _user(r) -> User:
    return User(**{f.name: r[f.name] for f in dc_fields(User)})


def _plan(r) -> Plan:
    data = {f.name: r[f.name] for f in dc_fields(Plan)}
    data["allowed_sources"] = list(data["allowed_sources"] or [])
    return Plan(**data)


def _sub(r) -> Subscription:
    data = {f.name: r[f.name] for f in dc_fields(Subscription)}
    data["status"] = SubscriptionStatus(data["status"])
    return Subscription(**data)


def _search(r) -> Search:
    criteria = SearchCriteria(
        keywords=list(r["keywords"] or []), alt_keywords=list(r["alt_keywords"] or []),
        excluded_keywords=list(r["excluded_keywords"] or []), category=r["category"], price_min=r["price_min"],
        price_max=r["price_max"], currency=r["currency"], condition=Condition(r["condition"]), city=r["city"],
        region=r["region"], radius_km=r["radius_km"], delivery=Delivery(r["delivery"]),
        include_unknown=r["include_unknown"], fuzzy=r["fuzzy"], match_description=r["match_description"],
    )
    return Search(
        id=r["id"], user_id=r["user_id"], name=r["name"], sources=list(r["sources"] or []), criteria=criteria,
        interval_seconds=r["interval_seconds"], is_active=r["is_active"], paused_reason=r["paused_reason"],
        baseline_done=r["baseline_done"], consecutive_failures=r["consecutive_failures"],
        last_checked_at=r["last_checked_at"], last_success_at=r["last_success_at"], next_check_at=r["next_check_at"],
        created_at=r["created_at"],
    )


def _listing(r) -> StoredListing:
    return StoredListing(**{f.name: r[f.name] for f in dc_fields(StoredListing)})


def _notification(r) -> Notification:
    data = {f.name: r[f.name] for f in dc_fields(Notification)}
    data["kind"] = NotificationKind(data["kind"])
    data["status"] = NotificationStatus(data["status"])
    return Notification(**data)


def _payment(r) -> Payment:
    return Payment(**{f.name: r[f.name] for f in dc_fields(Payment)})


def _source(r) -> SourceState:
    return SourceState(**{f.name: r[f.name] for f in dc_fields(SourceState)})


class SqlRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._conn: contextvars.ContextVar[AsyncConnection | None] = contextvars.ContextVar("tm_conn", default=None)

    # ------------------------------------------------------------ plumbing
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        if self._conn.get() is not None:
            yield
            return
        try:
            async with self.engine.begin() as conn:
                token = self._conn.set(conn)
                try:
                    yield
                finally:
                    self._conn.reset(token)
        except SAIntegrityError as exc:
            raise IntegrityError(str(exc.orig)) from exc

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        conn = self._conn.get()
        if conn is not None:
            yield conn
            return
        async with self.engine.begin() as conn:
            yield conn

    async def _exec(self, sql: str, **params: Any):
        params = {k: _plain(v) for k, v in params.items()}
        try:
            async with self._connection() as conn:
                result = await conn.execute(text(sql), params)
                if result.returns_rows:
                    return result.mappings().all()
                return result.rowcount
        except SAIntegrityError as exc:
            raise IntegrityError(str(exc.orig)) from exc

    async def _one(self, sql: str, **params: Any):
        rows = await self._exec(sql, **params)
        return rows[0] if rows else None

    # ---------------------------------------------------------------- users
    async def get_user(self, user_id: int) -> User | None:
        r = await self._one(q.GET_USER, id=user_id)
        return _user(r) if r else None

    async def get_user_by_telegram_id(self, telegram_id: int) -> User | None:
        r = await self._one(q.GET_USER_BY_TG, telegram_id=telegram_id)
        return _user(r) if r else None

    async def create_user(self, telegram_id, username, first_name, language, timezone, now) -> User:
        r = await self._one(q.CREATE_USER, telegram_id=telegram_id, username=username, first_name=first_name,
                            language=language, timezone=timezone, now=now)
        return _user(r)

    async def update_user(self, user_id: int, **fields: Any) -> None:
        if fields:
            await self._exec(q.build_update("users", fields, q.USER_UPDATABLE, "updated_at = now()"),
                             id=user_id, **fields)

    async def delete_user_data(self, user_id: int, now: datetime) -> None:
        async with self.transaction():
            for stmt in (q.DELETE_USER_SEARCHES, q.DELETE_USER_NOTIFICATIONS, q.DELETE_USER_MATCHES,
                         q.CANCEL_USER_SUBSCRIPTIONS):
                await self._exec(stmt, user_id=user_id)
            await self._exec(q.ANONYMIZE_USER, user_id=user_id, now=now)

    # ---------------------------------------------------------------- plans
    async def list_plans(self, active_only: bool = True) -> list[Plan]:
        return [_plan(r) for r in await self._exec(q.LIST_PLANS, active_only=active_only)]

    async def get_plan(self, plan_id: int) -> Plan | None:
        r = await self._one(q.GET_PLAN, id=plan_id)
        return _plan(r) if r else None

    async def get_plan_by_code(self, code: str) -> Plan | None:
        r = await self._one(q.GET_PLAN_BY_CODE, code=code)
        return _plan(r) if r else None

    async def upsert_plan(self, plan: Plan) -> Plan:
        data = {f.name: getattr(plan, f.name) for f in dc_fields(Plan) if f.name != "id"}
        await self._exec(q.INSERT_PLAN, **data)
        return await self.get_plan_by_code(plan.code)

    async def update_plan(self, code: str, **fields: Any) -> None:
        plan = await self.get_plan_by_code(code)
        if plan is None:
            raise KeyError(code)
        await self._exec(q.build_update("plans", fields, q.PLAN_UPDATABLE, "updated_at = now()"), id=plan.id, **fields)

    # -------------------------------------------------------- subscriptions
    async def get_active_subscription(self, user_id: int) -> Subscription | None:
        r = await self._one(q.GET_ACTIVE_SUB, user_id=user_id)
        return _sub(r) if r else None

    async def create_subscription(self, user_id, plan_id, origin, starts_at, expires_at, payment_id) -> Subscription:
        r = await self._one(q.CREATE_SUB, user_id=user_id, plan_id=plan_id, origin=origin, starts_at=starts_at,
                            expires_at=expires_at, payment_id=payment_id)
        return _sub(r)

    async def set_subscription_status(self, subscription_id, status, expires_at=None) -> None:
        await self._exec(q.SET_SUB_STATUS, id=subscription_id, status=status, expires_at=expires_at)

    async def list_expired_active_subscriptions(self, now, limit) -> list[Subscription]:
        return [_sub(r) for r in await self._exec(q.LIST_EXPIRED_ACTIVE, now=now, limit=limit)]

    async def mark_expiry_notified(self, subscription_id, now) -> None:
        await self._exec(q.MARK_EXPIRY_NOTIFIED, id=subscription_id, now=now)

    async def list_unnotified_expired(self, limit) -> list[Subscription]:
        return [_sub(r) for r in await self._exec(q.LIST_UNNOTIFIED_EXPIRED, limit=limit)]

    # ------------------------------------------------------------- searches
    async def create_search(self, search: Search) -> Search:
        c = search.criteria
        r = await self._one(
            q.CREATE_SEARCH, user_id=search.user_id, name=search.name, sources=list(search.sources),
            keywords=list(c.keywords), alt_keywords=list(c.alt_keywords), excluded_keywords=list(c.excluded_keywords),
            category=c.category, price_min=c.price_min, price_max=c.price_max, currency=c.currency,
            condition=c.condition, city=c.city, region=c.region, radius_km=c.radius_km, delivery=c.delivery,
            include_unknown=c.include_unknown, fuzzy=c.fuzzy, match_description=c.match_description,
            interval_seconds=search.interval_seconds,
            is_active=search.is_active, next_check_at=search.next_check_at, created_at=search.created_at,
        )
        return _search(r)

    async def get_search(self, search_id: int) -> Search | None:
        r = await self._one(q.GET_SEARCH, id=search_id)
        return _search(r) if r else None

    async def list_user_searches(self, user_id: int) -> list[Search]:
        return [_search(r) for r in await self._exec(q.LIST_USER_SEARCHES, user_id=user_id)]

    async def update_search(self, search_id: int, **fields: Any) -> None:
        if fields:
            await self._exec(q.build_update("searches", fields, q.SEARCH_UPDATABLE, "updated_at = now()"),
                             id=search_id, **fields)

    async def delete_search(self, search_id: int) -> None:
        await self._exec(q.DELETE_SEARCH, id=search_id)

    async def list_due_searches(self, now, limit) -> list[Search]:
        return [_search(r) for r in await self._exec(q.LIST_DUE_SEARCHES, now=now, limit=limit)]

    # -------------------------------------------------------------- listings
    async def upsert_listing(self, listing: Listing, now: datetime) -> UpsertResult:
        data = {f.name: getattr(listing, f.name) for f in dc_fields(Listing)}
        r = await self._one(q.UPSERT_LISTING, now=now, **data)
        return UpsertResult(_listing(r), bool(r["inserted"]), r["old_price"])

    async def get_listing(self, listing_id: int) -> StoredListing | None:
        r = await self._one(q.GET_LISTING, id=listing_id)
        return _listing(r) if r else None

    async def add_match(self, search_id, listing_id, user_id, reasons) -> bool:
        rows = await self._exec(q.ADD_MATCH, search_id=search_id, listing_id=listing_id, user_id=user_id,
                                reasons=json.dumps(reasons, ensure_ascii=False, default=str))
        return bool(rows)

    async def add_run(self, search_id, source, started_at, finished_at, status, fetched, new_listings, matched, error):
        await self._exec(q.ADD_RUN, search_id=search_id, source=source, started_at=started_at,
                         finished_at=finished_at, status=status, fetched=fetched, new_listings=new_listings,
                         matched=matched, error=error)

    # --------------------------------------------------------- notifications
    async def enqueue_notification(self, user_id, listing_id, search_id, kind, dedup_key, price, next_attempt_at) -> bool:
        rows = await self._exec(q.ENQUEUE_NOTIFICATION, user_id=user_id, listing_id=listing_id, search_id=search_id,
                                kind=kind, dedup_key=dedup_key, price=price, next_attempt_at=next_attempt_at)
        return bool(rows)

    async def claim_notifications(self, now, limit, stale_after_seconds=300) -> list[Notification]:
        rows = await self._exec(q.CLAIM_NOTIFICATIONS, now=now, stale=now - timedelta(seconds=stale_after_seconds),
                                limit=limit)
        return sorted((_notification(r) for r in rows), key=lambda n: (n.next_attempt_at, n.id))

    async def finish_notification(self, notification_id, status, now, error=None, message_id=None,
                                  next_attempt_at=None, count_attempt=True) -> None:
        await self._exec(q.FINISH_NOTIFICATION, id=notification_id, status=status, now=now, error=error,
                         message_id=message_id, next_attempt_at=next_attempt_at, attempt_inc=1 if count_attempt else 0)

    async def count_sent_since(self, user_id, since) -> int:
        r = await self._one(q.COUNT_SENT_SINCE, user_id=user_id, since=since)
        return int(r["count"])

    async def list_comparable_prices(self, search_id, exclude_listing_id, since, currency, condition,
                                     limit=500) -> list[Decimal]:
        rows = await self._exec(q.COMPARABLE_PRICES, search_id=search_id, listing_id=exclude_listing_id, since=since,
                                currency=currency, condition=condition, limit=limit)
        return [r["price"] for r in rows]

    # --------------------------------------------------------------- sources
    async def get_source_state(self, source: str) -> SourceState | None:
        r = await self._one(q.GET_SOURCE_STATE, source=source)
        return _source(r) if r else None

    async def save_source_state(self, state: SourceState, now: datetime) -> None:
        data = {f.name: getattr(state, f.name) for f in dc_fields(SourceState)}
        await self._exec(q.SAVE_SOURCE_STATE, now=now, **data)

    async def list_source_states(self) -> list[SourceState]:
        return [_source(r) for r in await self._exec(q.LIST_SOURCE_STATES)]

    # -------------------------------------------------------------- payments
    async def record_payment_event(self, provider, event_type, external_event_id, payload, now) -> tuple[int, bool]:
        rows = await self._exec(q.INSERT_PAYMENT_EVENT, provider=provider, event_type=event_type,
                                external_event_id=external_event_id,
                                payload=json.dumps(payload, ensure_ascii=False, default=str), now=now)
        if rows:
            return rows[0]["id"], True
        r = await self._one(q.GET_PAYMENT_EVENT_ID, provider=provider, external_event_id=external_event_id)
        return r["id"], False

    async def finish_payment_event(self, event_id, now, payment_id, error) -> None:
        await self._exec(q.FINISH_PAYMENT_EVENT, id=event_id, now=now, payment_id=payment_id, error=error)

    async def get_payment_by_charge_id(self, charge_id: str) -> Payment | None:
        r = await self._one(q.GET_PAYMENT_BY_CHARGE, charge_id=charge_id)
        return _payment(r) if r else None

    async def create_payment(self, payment: Payment) -> Payment:
        data = {f.name: getattr(payment, f.name) for f in dc_fields(Payment) if f.name not in {"id", "refunded_at"}}
        return _payment(await self._one(q.CREATE_PAYMENT, **data))

    async def update_payment(self, payment_id: int, **fields: Any) -> None:
        if fields:
            await self._exec(q.build_update("payments", fields, q.PAYMENT_UPDATABLE), id=payment_id, **fields)

    async def list_user_payments(self, user_id, limit=20) -> list[Payment]:
        return [_payment(r) for r in await self._exec(q.LIST_USER_PAYMENTS, user_id=user_id, limit=limit)]

    async def list_payment_errors(self, limit=20) -> list[dict]:
        return [dict(r) for r in await self._exec(q.LIST_PAYMENT_ERRORS, limit=limit)]

    async def create_refund(self, payment_id, amount, reason, status, admin_telegram_id, error, now) -> int:
        r = await self._one(q.CREATE_REFUND, payment_id=payment_id, amount=Decimal(amount), reason=reason,
                            status=status, admin_telegram_id=admin_telegram_id, error=error, now=now)
        return r["id"]

    # ------------------------------------------------------------------ ops
    async def add_audit(self, admin_telegram_id, action, target, details, now) -> None:
        await self._exec(q.ADD_AUDIT, admin_telegram_id=admin_telegram_id, action=action, target=target,
                         details=json.dumps(details, ensure_ascii=False, default=str), now=now)

    async def list_audit(self, limit=20) -> list[dict]:
        return [dict(r) for r in await self._exec(q.LIST_AUDIT, limit=limit)]

    async def stats(self, now) -> dict[str, int]:
        r = await self._one(q.STATS, now=now, day_ago=now - timedelta(days=1))
        return {k: int(v) for k, v in dict(r).items()}

    async def purge_old_data(self, listings_before, runs_before, notifications_before) -> dict[str, int]:
        async with self.transaction():
            n = await self._exec(q.PURGE_NOTIFICATIONS, before=notifications_before)
            listings = await self._exec(q.PURGE_LISTINGS, before=listings_before)
            runs = await self._exec(q.PURGE_RUNS, before=runs_before)
        return {"notifications": n, "listings": listings, "runs": runs}

    async def ping(self) -> bool:
        await self._exec("SELECT 1")
        return True
