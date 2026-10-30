"""Plain domain objects shared by services, storage backends and the bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Condition(str, Enum):
    ANY = "any"
    NEW = "new"
    USED = "used"


class Delivery(str, Enum):
    ANY = "any"
    SHIPPING = "shipping"
    PICKUP = "pickup"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    LIMITED = "limited"
    CANCELLED = "cancelled"


class NotificationKind(str, Enum):
    NEW = "new"
    PRICE_DROP = "price_drop"


@dataclass
class Listing:
    """A listing as returned by a source adapter (not yet stored)."""

    source: str
    external_id: str
    url: str
    title: str
    price: Decimal | None = None
    currency: str | None = "PLN"
    description: str | None = None
    city: str | None = None
    region: str | None = None
    condition: str | None = None  # 'new' | 'used' | None (unknown)
    category: str | None = None
    delivery_available: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    posted_at: datetime | None = None
    is_test: bool = False


@dataclass
class StoredListing(Listing):
    id: int = 0
    previous_price: Decimal | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    price_changed_at: datetime | None = None


@dataclass
class SearchCriteria:
    keywords: list[str] = field(default_factory=list)  # all must be present
    alt_keywords: list[str] = field(default_factory=list)  # at least one must be present
    excluded_keywords: list[str] = field(default_factory=list)
    category: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str = "PLN"
    condition: Condition = Condition.ANY
    city: str | None = None
    region: str | None = None
    radius_km: int | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    delivery: Delivery = Delivery.ANY
    include_unknown: bool = True
    fuzzy: bool = False
    match_description: bool = False  # keywords are searched in the title only unless enabled


@dataclass
class Search:
    id: int
    user_id: int
    name: str
    sources: list[str]
    criteria: SearchCriteria
    interval_seconds: int
    is_active: bool = True
    paused_reason: str | None = None
    baseline_done: bool = False
    consecutive_failures: int = 0
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    next_check_at: datetime | None = None
    created_at: datetime | None = None


@dataclass
class User:
    id: int
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    language: str = "pl"
    timezone: str = "Europe/Warsaw"
    is_suspended: bool = False
    suspended_reason: str | None = None
    trial_used: bool = False
    bot_blocked: bool = False
    notify_price_drops: bool = True
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None
    terms_accepted_at: datetime | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class Plan:
    code: str
    name: str
    price_stars: int
    duration_days: int
    max_active_searches: int
    allowed_sources: list[str]
    min_interval_seconds: int
    max_notifications_per_day: int
    advanced_filters: bool = False
    fuzzy_matching: bool = False
    price_drop_alerts: bool = False
    is_trial: bool = False
    is_active: bool = True
    sort_order: int = 0
    id: int = 0


@dataclass
class Subscription:
    id: int
    user_id: int
    plan_id: int
    status: SubscriptionStatus
    origin: str
    starts_at: datetime
    expires_at: datetime
    payment_id: int | None = None
    expiry_notified_at: datetime | None = None

    def is_valid_at(self, now: datetime) -> bool:
        return self.status is SubscriptionStatus.ACTIVE and self.starts_at <= now < self.expires_at


@dataclass
class Notification:
    id: int
    user_id: int
    listing_id: int
    search_id: int | None
    kind: NotificationKind
    dedup_key: str
    status: NotificationStatus
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    price_at_notify: Decimal | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None


@dataclass
class Payment:
    id: int
    user_id: int
    plan_id: int | None
    provider: str
    status: str
    amount: Decimal
    currency: str
    invoice_payload: str
    telegram_payment_charge_id: str | None = None
    provider_payment_charge_id: str | None = None
    external_id: str | None = None
    created_at: datetime | None = None
    paid_at: datetime | None = None
    refunded_at: datetime | None = None


@dataclass
class SourceState:
    source: str
    enabled: bool = True
    status: str = "ok"
    disabled_reason: str | None = None
    blocked_until: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
