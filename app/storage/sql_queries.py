"""All SQL used by ``SqlRepository`` (PostgreSQL dialect, named ``:params``).

Kept free of imports so ``scripts/check_sql.py`` can PREPARE every statement against a real
PostgreSQL to catch syntax/type errors without the Python driver stack.
Ambiguous parameters carry explicit CASTs (needed for server-side binding anyway).
"""

USER_COLUMNS = (
    "id, telegram_id, username, first_name, language, timezone, is_suspended, suspended_reason, trial_used, "
    "bot_blocked, notify_price_drops, hide_new_sellers, deal_city, deal_radius_km, quiet_start_hour, quiet_end_hour, "
    "terms_accepted_at, created_at, "
    "last_seen_at, deleted_at"
)

GET_USER = f"SELECT {USER_COLUMNS} FROM users WHERE id = :id"
GET_USER_BY_TG = f"SELECT {USER_COLUMNS} FROM users WHERE telegram_id = :telegram_id"
CREATE_USER = f"""
INSERT INTO users (telegram_id, username, first_name, language, timezone, created_at, updated_at, last_seen_at)
VALUES (:telegram_id, :username, :first_name, :language, :timezone, :now, :now, :now)
RETURNING {USER_COLUMNS}
"""
DELETE_USER_SEARCHES = "DELETE FROM searches WHERE user_id = :user_id"
DELETE_USER_NOTIFICATIONS = "DELETE FROM notifications WHERE user_id = :user_id"
DELETE_USER_MATCHES = "DELETE FROM match_results WHERE user_id = :user_id"
CANCEL_USER_SUBSCRIPTIONS = "UPDATE subscriptions SET status = 'cancelled' WHERE user_id = :user_id AND status = 'active'"
ANONYMIZE_USER = """
UPDATE users SET username = NULL, first_name = NULL, deleted_at = :now, quiet_start_hour = NULL,
       quiet_end_hour = NULL, updated_at = :now
WHERE id = :user_id
"""

PLAN_COLUMNS = (
    "id, code, name, price_stars, duration_days, max_active_searches, allowed_sources, min_interval_seconds, "
    "max_notifications_per_day, advanced_filters, fuzzy_matching, price_drop_alerts, is_trial, is_active, sort_order"
)
LIST_PLANS = f"""
SELECT {PLAN_COLUMNS} FROM plans
WHERE (NOT CAST(:active_only AS boolean)) OR is_active
ORDER BY sort_order, id
"""
GET_PLAN = f"SELECT {PLAN_COLUMNS} FROM plans WHERE id = :id"
GET_PLAN_BY_CODE = f"SELECT {PLAN_COLUMNS} FROM plans WHERE code = :code"
INSERT_PLAN = """
INSERT INTO plans (code, name, price_stars, duration_days, max_active_searches, allowed_sources,
                   min_interval_seconds, max_notifications_per_day, advanced_filters, fuzzy_matching,
                   price_drop_alerts, is_trial, is_active, sort_order)
VALUES (:code, :name, :price_stars, :duration_days, :max_active_searches, CAST(:allowed_sources AS text[]),
        :min_interval_seconds, :max_notifications_per_day, :advanced_filters, :fuzzy_matching,
        :price_drop_alerts, :is_trial, :is_active, :sort_order)
ON CONFLICT (code) DO NOTHING
"""

SUB_COLUMNS = "id, user_id, plan_id, status, origin, starts_at, expires_at, payment_id, expiry_notified_at"
GET_ACTIVE_SUB = f"SELECT {SUB_COLUMNS} FROM subscriptions WHERE user_id = :user_id AND status = 'active' LIMIT 1"
CREATE_SUB = f"""
INSERT INTO subscriptions (user_id, plan_id, status, origin, starts_at, expires_at, payment_id)
VALUES (:user_id, :plan_id, 'active', :origin, :starts_at, :expires_at, :payment_id)
RETURNING {SUB_COLUMNS}
"""
SET_SUB_STATUS = """
UPDATE subscriptions SET status = :status,
       expires_at = COALESCE(CAST(:expires_at AS timestamptz), expires_at)
WHERE id = :id
"""
LIST_EXPIRED_ACTIVE = f"""
SELECT {SUB_COLUMNS} FROM subscriptions WHERE status = 'active' AND expires_at <= :now
ORDER BY expires_at LIMIT :limit
"""
MARK_EXPIRY_NOTIFIED = "UPDATE subscriptions SET expiry_notified_at = :now WHERE id = :id"
LIST_UNNOTIFIED_EXPIRED = f"""
SELECT {SUB_COLUMNS} FROM subscriptions WHERE status = 'expired' AND expiry_notified_at IS NULL
ORDER BY id LIMIT :limit
"""

SEARCH_COLUMNS = (
    "id, user_id, name, sources, keywords, alt_keywords, excluded_keywords, category, price_min, price_max, "
    "currency, condition, city, region, radius_km, delivery, include_unknown, fuzzy, match_description, deal_min_discount, interval_seconds, is_active, "
    "paused_reason, baseline_done, consecutive_failures, last_checked_at, last_success_at, next_check_at, created_at"
)
CREATE_SEARCH = f"""
INSERT INTO searches (user_id, name, sources, keywords, alt_keywords, excluded_keywords, category, price_min,
                      price_max, currency, condition, city, region, radius_km, delivery, include_unknown, fuzzy,
                      match_description, deal_min_discount, interval_seconds, is_active, next_check_at, created_at, updated_at)
VALUES (:user_id, :name, CAST(:sources AS text[]), CAST(:keywords AS text[]), CAST(:alt_keywords AS text[]),
        CAST(:excluded_keywords AS text[]), :category, :price_min, :price_max, :currency, :condition, :city,
        :region, :radius_km, :delivery, :include_unknown, :fuzzy, :match_description, :deal_min_discount, :interval_seconds, :is_active, :next_check_at,
        :created_at, :created_at)
RETURNING {SEARCH_COLUMNS}
"""
GET_SEARCH = f"SELECT {SEARCH_COLUMNS} FROM searches WHERE id = :id"
LIST_USER_SEARCHES = f"SELECT {SEARCH_COLUMNS} FROM searches WHERE user_id = :user_id ORDER BY id"
DELETE_SEARCH = "DELETE FROM searches WHERE id = :id"
LIST_DUE_SEARCHES = f"""
SELECT {SEARCH_COLUMNS} FROM searches
WHERE is_active AND (next_check_at IS NULL OR next_check_at <= :now)
ORDER BY next_check_at NULLS FIRST, id
LIMIT :limit
"""

LISTING_COLUMNS = (
    "id, source, external_id, url, title, description, price, previous_price, currency, city, region, condition, "
    "category, delivery_available, latitude, longitude, posted_at, seller_created_at, seller_is_business, photo_url, is_test, "
    "first_seen_at, last_seen_at, "
    "price_changed_at, ai_note"
)
UPSERT_LISTING = f"""
WITH old AS (
    SELECT price FROM listings WHERE source = :source AND external_id = :external_id
), ins AS (
    INSERT INTO listings (source, external_id, url, title, description, price, currency, city, region, condition,
                          category, delivery_available, latitude, longitude, posted_at, seller_created_at,
                          seller_is_business, photo_url, is_test,
                          first_seen_at, last_seen_at)
    VALUES (:source, :external_id, :url, :title, :description, :price, :currency, :city, :region, :condition,
            :category, :delivery_available, :latitude, :longitude, :posted_at, :seller_created_at,
            :seller_is_business, :photo_url, :is_test, :now, :now)
    ON CONFLICT (source, external_id) DO UPDATE SET
        url = EXCLUDED.url,
        title = EXCLUDED.title,
        description = COALESCE(EXCLUDED.description, listings.description),
        currency = COALESCE(EXCLUDED.currency, listings.currency),
        city = COALESCE(EXCLUDED.city, listings.city),
        region = COALESCE(EXCLUDED.region, listings.region),
        condition = COALESCE(EXCLUDED.condition, listings.condition),
        category = COALESCE(EXCLUDED.category, listings.category),
        delivery_available = COALESCE(EXCLUDED.delivery_available, listings.delivery_available),
        latitude = COALESCE(EXCLUDED.latitude, listings.latitude),
        longitude = COALESCE(EXCLUDED.longitude, listings.longitude),
        seller_created_at = COALESCE(EXCLUDED.seller_created_at, listings.seller_created_at),
        seller_is_business = COALESCE(EXCLUDED.seller_is_business, listings.seller_is_business),
        photo_url = COALESCE(EXCLUDED.photo_url, listings.photo_url),
        previous_price = CASE WHEN EXCLUDED.price IS NOT NULL AND EXCLUDED.price IS DISTINCT FROM listings.price
                              THEN listings.price ELSE listings.previous_price END,
        price_changed_at = CASE WHEN EXCLUDED.price IS NOT NULL AND EXCLUDED.price IS DISTINCT FROM listings.price
                                THEN EXCLUDED.last_seen_at ELSE listings.price_changed_at END,
        price = COALESCE(EXCLUDED.price, listings.price),
        last_seen_at = EXCLUDED.last_seen_at
    RETURNING {LISTING_COLUMNS}, (xmax = 0) AS inserted
)
SELECT ins.*, (SELECT price FROM old) AS old_price FROM ins
"""
GET_LISTING = f"SELECT {LISTING_COLUMNS} FROM listings WHERE id = :id"
SET_LISTING_AI_NOTE = "UPDATE listings SET ai_note = :note WHERE id = :id"

ADD_MATCH = """
INSERT INTO match_results (search_id, listing_id, user_id, reasons)
VALUES (:search_id, :listing_id, :user_id, CAST(:reasons AS jsonb))
ON CONFLICT (search_id, listing_id) DO NOTHING
RETURNING id
"""
ADD_RUN = """
INSERT INTO monitoring_runs (search_id, source, started_at, finished_at, status, fetched, new_listings, matched, error)
VALUES (:search_id, :source, :started_at, :finished_at, :status, :fetched, :new_listings, :matched, :error)
"""

NOTIFICATION_COLUMNS = (
    "id, user_id, listing_id, search_id, kind, dedup_key, status, attempts, next_attempt_at, last_error, "
    "price_at_notify, created_at, sent_at"
)
ENQUEUE_NOTIFICATION = """
INSERT INTO notifications (user_id, listing_id, search_id, kind, dedup_key, status, next_attempt_at,
                           price_at_notify, created_at)
VALUES (:user_id, :listing_id, :search_id, :kind, :dedup_key, 'pending', :next_attempt_at, :price, :next_attempt_at)
ON CONFLICT (user_id, dedup_key) DO NOTHING
RETURNING id
"""
CLAIM_NOTIFICATIONS = f"""
UPDATE notifications SET status = 'sending', claimed_at = :now
WHERE id IN (
    SELECT id FROM notifications
    WHERE (status = 'pending' AND next_attempt_at <= :now)
       OR (status = 'sending' AND claimed_at <= :stale)
    ORDER BY next_attempt_at, id
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
)
RETURNING {NOTIFICATION_COLUMNS}
"""
FINISH_NOTIFICATION = """
UPDATE notifications SET
    status = CAST(:status AS text),
    attempts = attempts + :attempt_inc,
    last_error = :error,
    next_attempt_at = COALESCE(CAST(:next_attempt_at AS timestamptz), next_attempt_at),
    sent_at = CASE WHEN CAST(:status AS text) = 'sent' THEN CAST(:now AS timestamptz) ELSE sent_at END,
    telegram_message_id = COALESCE(CAST(:message_id AS bigint), telegram_message_id),
    claimed_at = NULL
WHERE id = :id
"""
COMPARABLE_PRICES = """
SELECT l.price FROM match_results m
JOIN listings l ON l.id = m.listing_id
WHERE m.search_id = :search_id
  AND l.id <> :listing_id
  AND l.price IS NOT NULL
  AND l.last_seen_at >= :since
  AND (CAST(:currency AS text) IS NULL OR l.currency = CAST(:currency AS text))
  AND (CAST(:condition AS text) IS NULL OR l.condition = CAST(:condition AS text))
ORDER BY l.last_seen_at DESC
LIMIT :limit
"""
ACTIVE_MATCHES = f"""
SELECT {", ".join("l." + c.strip() for c in LISTING_COLUMNS.split(","))}
FROM match_results m JOIN listings l ON l.id = m.listing_id
WHERE m.search_id = :search_id AND l.last_seen_at >= :seen_since
ORDER BY l.last_seen_at DESC LIMIT :limit
"""
UPSERT_SNAPSHOT = """
INSERT INTO market_snapshots (search_id, day, variant, count, median, p25, p75, min_price)
VALUES (:search_id, :day, :variant, :count, :median, :p25, :p75, :min_price)
ON CONFLICT (search_id, day, variant) DO UPDATE SET
    count = EXCLUDED.count, median = EXCLUDED.median, p25 = EXCLUDED.p25, p75 = EXCLUDED.p75,
    min_price = EXCLUDED.min_price
"""
LIST_SNAPSHOTS = """
SELECT search_id, day, variant, count, median, p25, p75, min_price FROM market_snapshots
WHERE search_id = :search_id AND variant = :variant AND day >= :since_day ORDER BY day
"""
USER_LISTING_MATCH = """
SELECT m.search_id FROM match_results m
WHERE m.user_id = :user_id AND m.listing_id = :listing_id
ORDER BY m.id DESC LIMIT 1
"""
RECENT_MATCHES = f"""
SELECT {", ".join("l." + c.strip() for c in LISTING_COLUMNS.split(","))}
FROM match_results m JOIN listings l ON l.id = m.listing_id
WHERE m.search_id = :search_id AND l.first_seen_at >= :since
ORDER BY m.id DESC LIMIT :limit
"""
LISTING_BY_MESSAGE = """
SELECT listing_id FROM notifications WHERE user_id = :user_id AND telegram_message_id = :message_id
ORDER BY id DESC LIMIT 1
"""
COUNT_SENT_SINCE = """
SELECT count(*) FROM notifications WHERE user_id = :user_id AND status = 'sent' AND sent_at >= :since
"""

SOURCE_COLUMNS = "source, enabled, status, disabled_reason, blocked_until, last_success_at, last_error_at, last_error"
GET_SOURCE_STATE = f"SELECT {SOURCE_COLUMNS} FROM source_states WHERE source = :source"
LIST_SOURCE_STATES = f"SELECT {SOURCE_COLUMNS} FROM source_states ORDER BY source"
SAVE_SOURCE_STATE = """
INSERT INTO source_states (source, enabled, status, disabled_reason, blocked_until, last_success_at, last_error_at,
                           last_error, updated_at)
VALUES (:source, :enabled, :status, :disabled_reason, :blocked_until, :last_success_at, :last_error_at,
        :last_error, :now)
ON CONFLICT (source) DO UPDATE SET
    enabled = EXCLUDED.enabled, status = EXCLUDED.status, disabled_reason = EXCLUDED.disabled_reason,
    blocked_until = EXCLUDED.blocked_until, last_success_at = EXCLUDED.last_success_at,
    last_error_at = EXCLUDED.last_error_at, last_error = EXCLUDED.last_error, updated_at = EXCLUDED.updated_at
"""

INSERT_PAYMENT_EVENT = """
INSERT INTO payment_events (provider, event_type, external_event_id, payload, received_at)
VALUES (:provider, :event_type, :external_event_id, CAST(:payload AS jsonb), :now)
ON CONFLICT (provider, external_event_id) DO NOTHING
RETURNING id
"""
GET_PAYMENT_EVENT_ID = "SELECT id FROM payment_events WHERE provider = :provider AND external_event_id = :external_event_id"
FINISH_PAYMENT_EVENT = """
UPDATE payment_events SET processed_at = :now, payment_id = :payment_id, error = :error WHERE id = :id
"""
PAYMENT_COLUMNS = (
    "id, user_id, plan_id, provider, status, amount, currency, invoice_payload, telegram_payment_charge_id, "
    "provider_payment_charge_id, external_id, created_at, paid_at, refunded_at"
)
GET_PAYMENT_BY_CHARGE = f"SELECT {PAYMENT_COLUMNS} FROM payments WHERE telegram_payment_charge_id = :charge_id"
CREATE_PAYMENT = f"""
INSERT INTO payments (user_id, plan_id, provider, status, amount, currency, invoice_payload,
                      telegram_payment_charge_id, provider_payment_charge_id, external_id, created_at, paid_at)
VALUES (:user_id, :plan_id, :provider, :status, :amount, :currency, :invoice_payload, :telegram_payment_charge_id,
        :provider_payment_charge_id, :external_id, :created_at, :paid_at)
RETURNING {PAYMENT_COLUMNS}
"""
LIST_USER_PAYMENTS = f"SELECT {PAYMENT_COLUMNS} FROM payments WHERE user_id = :user_id ORDER BY id DESC LIMIT :limit"
LIST_PAYMENT_ERRORS = """
SELECT id, provider, event_type, error, received_at FROM payment_events
WHERE error IS NOT NULL ORDER BY id DESC LIMIT :limit
"""
CREATE_REFUND = """
INSERT INTO refunds (payment_id, amount, reason, status, admin_telegram_id, error, created_at, completed_at)
VALUES (:payment_id, :amount, :reason, CAST(:status AS text), :admin_telegram_id, :error, :now,
        CASE WHEN CAST(:status AS text) = 'completed' THEN CAST(:now AS timestamptz) END)
RETURNING id
"""

ADD_AUDIT = """
INSERT INTO admin_audit_log (admin_telegram_id, action, target, details, created_at)
VALUES (:admin_telegram_id, :action, :target, CAST(:details AS jsonb), :now)
"""
LIST_AUDIT = """
SELECT id, admin_telegram_id, action, target, details, created_at FROM admin_audit_log ORDER BY id DESC LIMIT :limit
"""
STATS = """
SELECT
  (SELECT count(*) FROM users WHERE deleted_at IS NULL) AS users,
  (SELECT count(*) FROM subscriptions WHERE status = 'active' AND starts_at <= :now AND expires_at > :now)
      AS active_subscriptions,
  (SELECT count(*) FROM subscriptions WHERE status = 'active' AND starts_at <= :now AND expires_at > :now
      AND origin IN ('stars', 'crypto')) AS paid_subscriptions,
  (SELECT count(*) FROM searches) AS searches,
  (SELECT count(*) FROM searches WHERE is_active) AS active_searches,
  (SELECT count(*) FROM listings) AS listings,
  (SELECT count(*) FROM notifications WHERE status = 'sent' AND sent_at >= :day_ago) AS notifications_24h,
  (SELECT count(*) FROM notifications WHERE status IN ('pending', 'sending')) AS pending_notifications,
  (SELECT count(*) FROM payments WHERE status = 'paid') AS payments_paid,
  (SELECT count(*) FROM monitoring_runs WHERE status IN ('error', 'blocked') AND started_at >= :day_ago)
      AS runs_failed_24h
"""
PURGE_NOTIFICATIONS = "DELETE FROM notifications WHERE COALESCE(sent_at, created_at) < :before"
PURGE_LISTINGS = """
DELETE FROM listings l WHERE l.last_seen_at < :before
AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.listing_id = l.id)
"""
PURGE_RUNS = "DELETE FROM monitoring_runs WHERE started_at < :before"

# Columns that may be changed through dynamic UPDATEs (whitelists; values are always bound parameters).
USER_UPDATABLE = {
    "username", "first_name", "language", "timezone", "is_suspended", "suspended_reason", "trial_used",
    "bot_blocked", "notify_price_drops", "hide_new_sellers", "deal_city", "deal_radius_km", "quiet_start_hour", "quiet_end_hour", "terms_accepted_at",
    "last_seen_at", "deleted_at",
}
SEARCH_UPDATABLE = {
    "name", "sources", "keywords", "alt_keywords", "excluded_keywords", "category", "price_min", "price_max",
    "currency", "condition", "city", "region", "radius_km", "delivery", "include_unknown", "fuzzy", "match_description", "deal_min_discount",
    "interval_seconds", "is_active", "paused_reason", "baseline_done", "consecutive_failures", "last_checked_at",
    "last_success_at", "next_check_at",
}
PLAN_UPDATABLE = {
    "name", "price_stars", "duration_days", "max_active_searches", "allowed_sources", "min_interval_seconds",
    "max_notifications_per_day", "advanced_filters", "fuzzy_matching", "price_drop_alerts", "is_active", "sort_order",
}
PAYMENT_UPDATABLE = {"status", "paid_at", "refunded_at", "provider_payment_charge_id", "external_id"}
ARRAY_COLUMNS = {"sources", "keywords", "alt_keywords", "excluded_keywords", "allowed_sources"}


def build_update(table: str, fields: dict, allowed: set[str], extra_set: str = "") -> str:
    """UPDATE <table> SET col = :col ... WHERE id = :id — only whitelisted column names."""
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"cannot update {table}.{sorted(unknown)}")
    parts = [f"{col} = CAST(:{col} AS text[])" if col in ARRAY_COLUMNS else f"{col} = :{col}" for col in fields]
    if extra_set:
        parts.append(extra_set)
    return f"UPDATE {table} SET {', '.join(parts)} WHERE id = :id"
