"""Subscription plans and server-side access rules.

Default plans are seeded into the ``plans`` table (``app/services/plans.py``); after that the
database is the source of truth and admins can change limits with /plan_set.
Prices are placeholders in Telegram Stars (XTR) — set real ones before going live.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.models import Plan, Search, Subscription, User

DEFAULT_PLANS: list[Plan] = [
    Plan(
        code="trial",
        name="Free Trial",
        price_stars=0,
        duration_days=3,
        max_active_searches=2,
        allowed_sources=["mock", "olx", "allegro_lokalnie"],
        min_interval_seconds=900,
        max_notifications_per_day=30,
        is_trial=True,
        sort_order=0,
    ),
    Plan(
        code="basic",
        name="Basic",
        price_stars=150,
        duration_days=30,
        max_active_searches=5,
        allowed_sources=["mock", "olx", "allegro_lokalnie"],
        min_interval_seconds=600,
        max_notifications_per_day=100,
        sort_order=1,
    ),
    Plan(
        code="pro",
        name="Pro",
        price_stars=350,
        duration_days=30,
        max_active_searches=15,
        allowed_sources=["mock", "olx", "allegro_lokalnie"],
        min_interval_seconds=300,
        max_notifications_per_day=300,
        advanced_filters=True,
        fuzzy_matching=True,
        price_drop_alerts=True,
        sort_order=2,
    ),
    Plan(
        code="premium",
        name="Premium",
        price_stars=700,
        duration_days=30,
        max_active_searches=40,
        allowed_sources=["mock", "olx", "allegro_lokalnie"],
        min_interval_seconds=120,
        max_notifications_per_day=1000,
        advanced_filters=True,
        fuzzy_matching=True,
        price_drop_alerts=True,
        sort_order=3,
    ),
    # Personal mode only (PERSONAL_MODE=true): never sold, given to the owner (admin) automatically.
    Plan(
        code="personal",
        name="Personal",
        price_stars=0,
        duration_days=3650,
        max_active_searches=20,
        allowed_sources=["mock", "olx"],
        min_interval_seconds=600,
        max_notifications_per_day=500,
        advanced_filters=True,
        fuzzy_matching=True,
        price_drop_alerts=True,
        is_active=False,
        sort_order=9,
    ),
]

EDITABLE_PLAN_FIELDS = {
    "name": str,
    "price_stars": int,
    "duration_days": int,
    "max_active_searches": int,
    "min_interval_seconds": int,
    "max_notifications_per_day": int,
    "advanced_filters": bool,
    "fuzzy_matching": bool,
    "price_drop_alerts": bool,
    "is_active": bool,
    "allowed_sources": list,
}


class AccessDenied(Exception):
    """Raised by server-side access checks. ``code`` maps to an i18n key ``deny.<code>``."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass
class Access:
    user: User
    subscription: Subscription | None
    plan: Plan | None
    now: datetime

    @property
    def active(self) -> bool:
        return (
            not self.user.is_suspended
            and self.user.deleted_at is None
            and self.subscription is not None
            and self.plan is not None
            and self.subscription.is_valid_at(self.now)
        )

    def require_active(self) -> Plan:
        if self.user.is_suspended:
            raise AccessDenied("suspended")
        if not self.active or self.plan is None:
            raise AccessDenied("no_subscription")
        return self.plan

    def check_can_create_search(self, active_count: int) -> Plan:
        plan = self.require_active()
        if active_count >= plan.max_active_searches:
            raise AccessDenied("search_limit", limit=plan.max_active_searches)
        return plan

    def check_sources(self, sources: list[str]) -> None:
        plan = self.require_active()
        forbidden = [s for s in sources if s not in plan.allowed_sources]
        if forbidden:
            raise AccessDenied("source_not_in_plan", sources=", ".join(forbidden))

    def clamp_interval(self, requested_seconds: int | None) -> int:
        plan = self.require_active()
        if requested_seconds is None:
            return plan.min_interval_seconds
        return max(int(requested_seconds), plan.min_interval_seconds)

    def check_advanced(self, fuzzy: bool) -> None:
        plan = self.require_active()
        if fuzzy and not plan.fuzzy_matching:
            raise AccessDenied("feature_not_in_plan", feature="fuzzy")


def monitored_search_ids(searches: list[Search], plan: Plan | None) -> set[int]:
    """Which active searches may run under ``plan`` (oldest first, up to the plan limit).

    After a downgrade the newest searches over the limit are kept but not monitored.
    """
    if plan is None:
        return set()
    active = sorted(
        (s for s in searches if s.is_active),
        key=lambda s: (s.created_at or datetime.min, s.id),
    )
    return {s.id for s in active[: plan.max_active_searches]}


def compute_new_period(
    now: datetime,
    plan: Plan,
    current: Subscription | None,
    current_plan: Plan | None,
) -> tuple[datetime, datetime, int]:
    """Return (starts_at, expires_at, bonus_seconds) for a purchase of ``plan``.

    * same plan, still active: renewal is appended to the current period;
    * different plan, still active: the new plan starts now and the unused value of the
      old plan is converted into extra time of the new plan (pro-rata by price);
    * otherwise: starts now.
    """
    duration = timedelta(days=plan.duration_days)
    if current is None or not current.is_valid_at(now) or current_plan is None:
        return now, now + duration, 0
    if current_plan.code == plan.code:
        return now, current.expires_at + duration, 0
    remaining = (current.expires_at - now).total_seconds()
    bonus = 0
    if remaining > 0 and plan.price_stars > 0 and current_plan.price_stars > 0:
        old_rate = Decimal(current_plan.price_stars) / Decimal(current_plan.duration_days * 86400)
        new_rate = Decimal(plan.price_stars) / Decimal(plan.duration_days * 86400)
        bonus = int(Decimal(remaining) * old_rate / new_rate)
    return now, now + duration + timedelta(seconds=bonus), bonus
