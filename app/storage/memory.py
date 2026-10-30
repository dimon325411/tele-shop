"""In-memory repository emulating the PostgreSQL schema constraints.

Used by the unit tests (no database needed) and handy for local experiments.
Every public coroutine yields to the event loop first so that concurrency tests
interleave like with a real database.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import fields as dc_fields
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncIterator

from app.domain.models import (
    Listing,
    Notification,
    NotificationKind,
    NotificationStatus,
    Payment,
    Plan,
    Search,
    SourceState,
    StoredListing,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.storage.errors import IntegrityError
from app.storage.repo import UpsertResult


def _c(obj):
    return copy.deepcopy(obj)


class MemoryRepository:
    def __init__(self) -> None:
        self._ids: dict[str, int] = {}
        self.users: dict[int, User] = {}
        self.plans: dict[int, Plan] = {}
        self.subscriptions: dict[int, Subscription] = {}
        self.searches: dict[int, Search] = {}
        self.listings: dict[int, StoredListing] = {}
        self.listing_keys: dict[tuple[str, str], int] = {}
        self.matches: dict[tuple[int, int], dict] = {}
        self.runs: list[dict] = []
        self.notifications: dict[int, Notification] = {}
        self.notification_keys: dict[tuple[int, str], int] = {}
        self.source_states: dict[str, SourceState] = {}
        self.payments: dict[int, Payment] = {}
        self.payment_events: dict[int, dict] = {}
        self.payment_event_keys: dict[tuple[str, str], int] = {}
        self.refunds: dict[int, dict] = {}
        self.audit: list[dict] = []
        self.snapshots: dict[tuple, dict] = {}
        self._tx_lock = asyncio.Lock()
        self._tx_owner: asyncio.Task | None = None

    def _next(self, name: str) -> int:
        self._ids[name] = self._ids.get(name, 0) + 1
        return self._ids[name]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Serialises transactions; on error restores a snapshot (rollback)."""
        task = asyncio.current_task()
        if self._tx_owner is task:  # nested: join the outer transaction
            yield
            return
        async with self._tx_lock:
            self._tx_owner = task
            snapshot = {k: copy.deepcopy(v) for k, v in self.__dict__.items() if not k.startswith("_tx")}
            try:
                yield
            except BaseException:
                for k, v in snapshot.items():
                    setattr(self, k, v)
                raise
            finally:
                self._tx_owner = None

    # ----------------------------------------------------------------- users
    async def get_user(self, user_id: int) -> User | None:
        await asyncio.sleep(0)
        return _c(self.users.get(user_id))

    async def get_user_by_telegram_id(self, telegram_id: int) -> User | None:
        await asyncio.sleep(0)
        return _c(next((u for u in self.users.values() if u.telegram_id == telegram_id), None))

    async def create_user(self, telegram_id, username, first_name, language, timezone, now) -> User:
        await asyncio.sleep(0)
        if any(u.telegram_id == telegram_id for u in self.users.values()):
            raise IntegrityError("users.telegram_id")
        user = User(id=self._next("user"), telegram_id=telegram_id, username=username, first_name=first_name,
                    language=language, timezone=timezone, created_at=now)
        self.users[user.id] = user
        return _c(user)

    async def update_user(self, user_id: int, **fields: Any) -> None:
        await asyncio.sleep(0)
        user = self.users[user_id]
        for key, value in fields.items():
            if not hasattr(user, key):
                raise AttributeError(key)
            setattr(user, key, value)

    async def delete_user_data(self, user_id: int, now: datetime) -> None:
        await asyncio.sleep(0)
        for sid in [s.id for s in self.searches.values() if s.user_id == user_id]:
            await self.delete_search(sid)
        for nid in [n.id for n in self.notifications.values() if n.user_id == user_id]:
            n = self.notifications.pop(nid)
            self.notification_keys.pop((n.user_id, n.dedup_key), None)
        for key in [k for k, m in self.matches.items() if m["user_id"] == user_id]:
            del self.matches[key]
        for sub in self.subscriptions.values():
            if sub.user_id == user_id and sub.status is SubscriptionStatus.ACTIVE:
                sub.status = SubscriptionStatus.CANCELLED
        user = self.users[user_id]
        user.username = None
        user.first_name = None
        user.deleted_at = now
        user.quiet_start_hour = user.quiet_end_hour = None

    # ----------------------------------------------------------------- plans
    async def list_plans(self, active_only: bool = True) -> list[Plan]:
        await asyncio.sleep(0)
        plans = [p for p in self.plans.values() if p.is_active or not active_only]
        return _c(sorted(plans, key=lambda p: (p.sort_order, p.id)))

    async def get_plan(self, plan_id: int) -> Plan | None:
        return _c(self.plans.get(plan_id))

    async def get_plan_by_code(self, code: str) -> Plan | None:
        return _c(next((p for p in self.plans.values() if p.code == code), None))

    async def upsert_plan(self, plan: Plan) -> Plan:
        await asyncio.sleep(0)
        existing = next((p for p in self.plans.values() if p.code == plan.code), None)
        if existing:
            return _c(existing)  # seeding never overwrites admin changes
        stored = _c(plan)
        stored.id = self._next("plan")
        self.plans[stored.id] = stored
        return _c(stored)

    async def update_plan(self, code: str, **fields: Any) -> None:
        plan = next((p for p in self.plans.values() if p.code == code), None)
        if plan is None:
            raise KeyError(code)
        for key, value in fields.items():
            setattr(plan, key, value)

    # --------------------------------------------------------- subscriptions
    async def get_active_subscription(self, user_id: int) -> Subscription | None:
        await asyncio.sleep(0)
        return _c(next((s for s in self.subscriptions.values()
                        if s.user_id == user_id and s.status is SubscriptionStatus.ACTIVE), None))

    async def create_subscription(self, user_id, plan_id, origin, starts_at, expires_at, payment_id) -> Subscription:
        await asyncio.sleep(0)
        if any(s.user_id == user_id and s.status is SubscriptionStatus.ACTIVE for s in self.subscriptions.values()):
            raise IntegrityError("ux_subscriptions_one_active")
        if expires_at <= starts_at:
            raise IntegrityError("subscriptions.expires_after_start")
        sub = Subscription(id=self._next("sub"), user_id=user_id, plan_id=plan_id, status=SubscriptionStatus.ACTIVE,
                           origin=origin, starts_at=starts_at, expires_at=expires_at, payment_id=payment_id)
        self.subscriptions[sub.id] = sub
        return _c(sub)

    async def set_subscription_status(self, subscription_id, status, expires_at=None) -> None:
        sub = self.subscriptions[subscription_id]
        sub.status = SubscriptionStatus(status)
        if expires_at is not None:
            sub.expires_at = expires_at

    async def list_expired_active_subscriptions(self, now, limit) -> list[Subscription]:
        subs = [s for s in self.subscriptions.values() if s.status is SubscriptionStatus.ACTIVE and s.expires_at <= now]
        return _c(subs[:limit])

    async def mark_expiry_notified(self, subscription_id, now) -> None:
        self.subscriptions[subscription_id].expiry_notified_at = now

    async def list_unnotified_expired(self, limit) -> list[Subscription]:
        subs = [s for s in self.subscriptions.values()
                if s.status is SubscriptionStatus.EXPIRED and s.expiry_notified_at is None]
        return _c(subs[:limit])

    # -------------------------------------------------------------- searches
    async def create_search(self, search: Search) -> Search:
        await asyncio.sleep(0)
        if not search.criteria.keywords and not search.criteria.alt_keywords:
            raise IntegrityError("searches.keywords")
        stored = _c(search)
        stored.id = self._next("search")
        self.searches[stored.id] = stored
        return _c(stored)

    async def get_search(self, search_id: int) -> Search | None:
        await asyncio.sleep(0)
        return _c(self.searches.get(search_id))

    async def list_user_searches(self, user_id: int) -> list[Search]:
        return _c(sorted((s for s in self.searches.values() if s.user_id == user_id), key=lambda s: s.id))

    async def update_search(self, search_id: int, **fields: Any) -> None:
        await asyncio.sleep(0)
        search = self.searches[search_id]
        for key, value in fields.items():
            if hasattr(search, key):
                setattr(search, key, value)
            elif hasattr(search.criteria, key):
                setattr(search.criteria, key, value)
            else:
                raise AttributeError(key)

    async def delete_search(self, search_id: int) -> None:
        self.searches.pop(search_id, None)
        for key in [k for k in self.snapshots if k[0] == search_id]:
            del self.snapshots[key]
        for key in [k for k in self.matches if k[0] == search_id]:
            del self.matches[key]
        for n in self.notifications.values():
            if n.search_id == search_id:
                n.search_id = None
        self.runs = [r for r in self.runs if r["search_id"] != search_id]

    async def list_due_searches(self, now, limit) -> list[Search]:
        due = [s for s in self.searches.values()
               if s.is_active and (s.next_check_at is None or s.next_check_at <= now)]
        due.sort(key=lambda s: (s.next_check_at or datetime.min.replace(tzinfo=now.tzinfo), s.id))
        return _c(due[:limit])

    # -------------------------------------------------------------- listings
    async def upsert_listing(self, listing: Listing, now: datetime) -> UpsertResult:
        await asyncio.sleep(0)
        key = (listing.source, listing.external_id)
        if key not in self.listing_keys:
            values = {f.name: getattr(listing, f.name) for f in dc_fields(Listing)}
            stored = StoredListing(**values, id=self._next("listing"), first_seen_at=now, last_seen_at=now)
            self.listings[stored.id] = stored
            self.listing_keys[key] = stored.id
            return UpsertResult(_c(stored), True, None)
        stored = self.listings[self.listing_keys[key]]
        old_price = stored.price
        stored.last_seen_at = now
        stored.title = listing.title
        stored.url = listing.url
        for name in ("description", "city", "region", "condition", "category", "delivery_available",
                     "latitude", "longitude", "currency", "seller_created_at", "seller_is_business", "photo_url"):
            new = getattr(listing, name)
            if new is not None:
                setattr(stored, name, new)
        if listing.price is not None and listing.price != stored.price:
            stored.previous_price = stored.price
            stored.price = listing.price
            stored.price_changed_at = now
        return UpsertResult(_c(stored), False, old_price)

    async def get_listing(self, listing_id: int) -> StoredListing | None:
        return _c(self.listings.get(listing_id))

    async def add_match(self, search_id, listing_id, user_id, reasons) -> bool:
        await asyncio.sleep(0)
        if (search_id, listing_id) in self.matches:
            return False
        self.matches[(search_id, listing_id)] = {"user_id": user_id, "reasons": _c(reasons), "seq": self._next("match")}
        return True

    async def add_run(self, search_id, source, started_at, finished_at, status, fetched, new_listings, matched, error):
        self.runs.append(dict(search_id=search_id, source=source, started_at=started_at, finished_at=finished_at,
                              status=status, fetched=fetched, new_listings=new_listings, matched=matched, error=error))

    # --------------------------------------------------------- notifications
    async def enqueue_notification(self, user_id, listing_id, search_id, kind, dedup_key, price, next_attempt_at) -> bool:
        await asyncio.sleep(0)
        if (user_id, dedup_key) in self.notification_keys:
            return False
        n = Notification(id=self._next("notification"), user_id=user_id, listing_id=listing_id, search_id=search_id,
                         kind=NotificationKind(kind), dedup_key=dedup_key, status=NotificationStatus.PENDING,
                         next_attempt_at=next_attempt_at, price_at_notify=price, created_at=next_attempt_at)
        self.notifications[n.id] = n
        self.notification_keys[(user_id, dedup_key)] = n.id
        return True

    async def claim_notifications(self, now, limit, stale_after_seconds=300) -> list[Notification]:
        await asyncio.sleep(0)
        stale = now - timedelta(seconds=stale_after_seconds)
        claimed = []
        for n in sorted(self.notifications.values(), key=lambda x: (x.next_attempt_at, x.id)):
            if len(claimed) >= limit:
                break
            ready = n.status is NotificationStatus.PENDING and n.next_attempt_at <= now
            stuck = n.status is NotificationStatus.SENDING and getattr(n, "_claimed_at", now) <= stale
            if ready or stuck:
                n.status = NotificationStatus.SENDING
                n._claimed_at = now  # type: ignore[attr-defined]
                claimed.append(_c(n))
        return claimed

    async def finish_notification(self, notification_id, status, now, error=None, message_id=None,
                                  next_attempt_at=None, count_attempt=True) -> None:
        n = self.notifications[notification_id]
        n.status = NotificationStatus(status)
        if count_attempt:
            n.attempts += 1
        n.last_error = error
        if next_attempt_at is not None:
            n.next_attempt_at = next_attempt_at
        if status == NotificationStatus.SENT.value:
            n.sent_at = now
            n.message_id = message_id  # type: ignore[attr-defined]

    async def count_sent_since(self, user_id, since) -> int:
        return sum(1 for n in self.notifications.values()
                   if n.user_id == user_id and n.status is NotificationStatus.SENT and n.sent_at and n.sent_at >= since)

    async def list_active_matches(self, search_id, seen_since, limit):
        rows = [self.listings[lid] for (sid, lid) in self.matches
                if sid == search_id and lid in self.listings and self.listings[lid].last_seen_at >= seen_since]
        rows.sort(key=lambda l: l.last_seen_at, reverse=True)
        return _c(rows[:limit])

    async def upsert_snapshot(self, search_id, day, variant, count, median, p25, p75, min_price):
        self.snapshots[(search_id, day, variant)] = dict(search_id=search_id, day=day, variant=variant, count=count,
                                                         median=median, p25=p25, p75=p75, min_price=min_price)

    async def list_snapshots(self, search_id, variant, since_day):
        rows = [r for (sid, day, v), r in self.snapshots.items() if sid == search_id and v == variant and day >= since_day]
        return _c(sorted(rows, key=lambda r: r["day"]))

    async def find_user_listing_match(self, user_id, listing_id):
        hits = [sid for (sid, lid), m in self.matches.items() if lid == listing_id and m["user_id"] == user_id]
        if not hits or listing_id not in self.listings:
            return None
        return max(hits), _c(self.listings[listing_id])

    async def list_recent_matches(self, search_id, since, limit):
        rows = [self.listings[lid] for (sid, lid), m in sorted(self.matches.items(), key=lambda kv: -kv[1]["seq"])
                if sid == search_id and lid in self.listings and self.listings[lid].first_seen_at >= since]
        return _c(rows[:limit])

    async def find_listing_by_message(self, user_id, telegram_message_id):
        for n in self.notifications.values():
            if n.user_id == user_id and getattr(n, "message_id", None) == telegram_message_id:
                return n.listing_id
        return None

    async def list_comparable_prices(self, search_id, exclude_listing_id, since, currency, condition,
                                     limit=500) -> list[Decimal]:
        out = []
        for (sid, lid) in self.matches:
            if sid != search_id or lid == exclude_listing_id:
                continue
            l = self.listings.get(lid)
            if l is None or l.price is None or l.last_seen_at < since:
                continue
            if currency is not None and l.currency != currency:
                continue
            if condition is not None and l.condition != condition:
                continue
            out.append(l.price)
        return out[:limit]

    # --------------------------------------------------------------- sources
    async def get_source_state(self, source: str) -> SourceState | None:
        return _c(self.source_states.get(source))

    async def save_source_state(self, state: SourceState, now: datetime) -> None:
        self.source_states[state.source] = _c(state)

    async def list_source_states(self) -> list[SourceState]:
        return _c(sorted(self.source_states.values(), key=lambda s: s.source))

    # -------------------------------------------------------------- payments
    async def record_payment_event(self, provider, event_type, external_event_id, payload, now) -> tuple[int, bool]:
        await asyncio.sleep(0)
        key = (provider, external_event_id)
        if key in self.payment_event_keys:
            return self.payment_event_keys[key], False
        eid = self._next("payment_event")
        self.payment_events[eid] = dict(id=eid, provider=provider, event_type=event_type,
                                        external_event_id=external_event_id, payload=_c(payload),
                                        received_at=now, processed_at=None, error=None, payment_id=None)
        self.payment_event_keys[key] = eid
        return eid, True

    async def finish_payment_event(self, event_id, now, payment_id, error) -> None:
        ev = self.payment_events[event_id]
        ev.update(processed_at=now, payment_id=payment_id, error=error)

    async def get_payment_by_charge_id(self, charge_id: str) -> Payment | None:
        return _c(next((p for p in self.payments.values() if p.telegram_payment_charge_id == charge_id), None))

    async def create_payment(self, payment: Payment) -> Payment:
        await asyncio.sleep(0)
        if payment.telegram_payment_charge_id and any(
            p.telegram_payment_charge_id == payment.telegram_payment_charge_id for p in self.payments.values()
        ):
            raise IntegrityError("payments.telegram_payment_charge_id")
        stored = _c(payment)
        stored.id = self._next("payment")
        self.payments[stored.id] = stored
        return _c(stored)

    async def update_payment(self, payment_id: int, **fields: Any) -> None:
        p = self.payments[payment_id]
        for key, value in fields.items():
            setattr(p, key, value)

    async def list_user_payments(self, user_id, limit=20) -> list[Payment]:
        items = sorted((p for p in self.payments.values() if p.user_id == user_id), key=lambda p: -p.id)
        return _c(items[:limit])

    async def list_payment_errors(self, limit=20) -> list[dict]:
        errs = [e for e in self.payment_events.values() if e["error"]]
        errs.sort(key=lambda e: -e["id"])
        return [dict(id=e["id"], provider=e["provider"], event_type=e["event_type"], error=e["error"],
                     received_at=e["received_at"]) for e in errs[:limit]]

    async def create_refund(self, payment_id, amount, reason, status, admin_telegram_id, error, now) -> int:
        if status == "completed" and any(r["payment_id"] == payment_id and r["status"] == "completed"
                                         for r in self.refunds.values()):
            raise IntegrityError("ux_refunds_completed")
        rid = self._next("refund")
        self.refunds[rid] = dict(id=rid, payment_id=payment_id, amount=amount, reason=reason, status=status,
                                 admin_telegram_id=admin_telegram_id, error=error, created_at=now,
                                 completed_at=now if status == "completed" else None)
        return rid

    # ------------------------------------------------------------------ ops
    async def add_audit(self, admin_telegram_id, action, target, details, now) -> None:
        self.audit.append(dict(id=len(self.audit) + 1, admin_telegram_id=admin_telegram_id, action=action,
                               target=target, details=_c(details), created_at=now))

    async def list_audit(self, limit=20) -> list[dict]:
        return _c(list(reversed(self.audit))[:limit])

    async def stats(self, now) -> dict[str, int]:
        day_ago = now - timedelta(days=1)
        return {
            "users": sum(1 for u in self.users.values() if u.deleted_at is None),
            "active_subscriptions": sum(1 for s in self.subscriptions.values() if s.is_valid_at(now)),
            "paid_subscriptions": sum(1 for s in self.subscriptions.values()
                                      if s.is_valid_at(now) and s.origin in ("stars", "crypto")),
            "searches": len(self.searches),
            "active_searches": sum(1 for s in self.searches.values() if s.is_active),
            "listings": len(self.listings),
            "notifications_24h": sum(1 for n in self.notifications.values()
                                     if n.status is NotificationStatus.SENT and n.sent_at and n.sent_at >= day_ago),
            "pending_notifications": sum(1 for n in self.notifications.values()
                                         if n.status in (NotificationStatus.PENDING, NotificationStatus.SENDING)),
            "payments_paid": sum(1 for p in self.payments.values() if p.status == "paid"),
            "runs_failed_24h": sum(1 for r in self.runs if r["status"] in ("error", "blocked")
                                   and r["started_at"] >= day_ago),
        }

    async def purge_old_data(self, listings_before, runs_before, notifications_before) -> dict[str, int]:
        old_n = [n.id for n in self.notifications.values() if (n.sent_at or n.created_at) < notifications_before]
        for nid in old_n:
            n = self.notifications.pop(nid)
            self.notification_keys.pop((n.user_id, n.dedup_key), None)
        referenced = {n.listing_id for n in self.notifications.values()}
        old_l = [l.id for l in self.listings.values() if l.last_seen_at < listings_before and l.id not in referenced]
        for lid in old_l:
            l = self.listings.pop(lid)
            self.listing_keys.pop((l.source, l.external_id), None)
            for key in [k for k in self.matches if k[1] == lid]:
                del self.matches[key]
        before = len(self.runs)
        self.runs = [r for r in self.runs if r["started_at"] >= runs_before]
        return {"notifications": len(old_n), "listings": len(old_l), "runs": before - len(self.runs)}
