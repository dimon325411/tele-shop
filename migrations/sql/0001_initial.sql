-- Initial schema for telegram-market. All timestamps are UTC (timestamptz).
-- Applied by Alembic revision 0001 (migrations/versions/0001_initial.py).

CREATE TABLE users (
    id               BIGSERIAL PRIMARY KEY,
    telegram_id      BIGINT NOT NULL UNIQUE,
    username         TEXT,
    first_name       TEXT,
    language         TEXT NOT NULL DEFAULT 'pl',
    timezone         TEXT NOT NULL DEFAULT 'Europe/Warsaw',
    is_suspended     BOOLEAN NOT NULL DEFAULT FALSE,
    suspended_reason TEXT,
    trial_used       BOOLEAN NOT NULL DEFAULT FALSE,
    bot_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    notify_price_drops BOOLEAN NOT NULL DEFAULT TRUE,
    quiet_start_hour SMALLINT CHECK (quiet_start_hour BETWEEN 0 AND 23),
    quiet_end_hour   SMALLINT CHECK (quiet_end_hour BETWEEN 0 AND 23),
    terms_accepted_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ,
    deleted_at       TIMESTAMPTZ
);

CREATE TABLE plans (
    id                        SERIAL PRIMARY KEY,
    code                      TEXT NOT NULL UNIQUE,
    name                      TEXT NOT NULL,
    price_stars               INTEGER NOT NULL CHECK (price_stars >= 0),
    duration_days             INTEGER NOT NULL CHECK (duration_days > 0),
    max_active_searches       INTEGER NOT NULL CHECK (max_active_searches >= 0),
    allowed_sources           TEXT[] NOT NULL,
    min_interval_seconds      INTEGER NOT NULL CHECK (min_interval_seconds >= 30),
    max_notifications_per_day INTEGER NOT NULL CHECK (max_notifications_per_day >= 0),
    advanced_filters          BOOLEAN NOT NULL DEFAULT FALSE,
    fuzzy_matching            BOOLEAN NOT NULL DEFAULT FALSE,
    price_drop_alerts         BOOLEAN NOT NULL DEFAULT FALSE,
    is_trial                  BOOLEAN NOT NULL DEFAULT FALSE,
    is_active                 BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order                INTEGER NOT NULL DEFAULT 0,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payments (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    plan_id                     INTEGER REFERENCES plans(id) ON DELETE SET NULL,
    provider                    TEXT NOT NULL CHECK (provider IN ('telegram_stars', 'crypto', 'mock')),
    status                      TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'refunded', 'failed')),
    amount                      NUMERIC(18, 8) NOT NULL CHECK (amount >= 0),
    currency                    TEXT NOT NULL,
    invoice_payload             TEXT NOT NULL,
    telegram_payment_charge_id  TEXT UNIQUE,
    provider_payment_charge_id  TEXT,
    external_id                 TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at                     TIMESTAMPTZ,
    refunded_at                 TIMESTAMPTZ,
    UNIQUE (provider, external_id)
);
CREATE INDEX ix_payments_user ON payments (user_id, created_at DESC);

CREATE TABLE subscriptions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id             INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
    status              TEXT NOT NULL CHECK (status IN ('active', 'expired', 'cancelled', 'refunded')),
    origin              TEXT NOT NULL CHECK (origin IN ('trial', 'stars', 'admin', 'crypto', 'mock')),
    starts_at           TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    payment_id          BIGINT REFERENCES payments(id) ON DELETE SET NULL,
    expiry_notified_at  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > starts_at)
);
-- At most one active subscription per user.
CREATE UNIQUE INDEX ux_subscriptions_one_active ON subscriptions (user_id) WHERE status = 'active';
CREATE INDEX ix_subscriptions_expiry ON subscriptions (status, expires_at);

CREATE TABLE searches (
    id                   BIGSERIAL PRIMARY KEY,
    user_id              BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    sources              TEXT[] NOT NULL,
    keywords             TEXT[] NOT NULL DEFAULT '{}',
    alt_keywords         TEXT[] NOT NULL DEFAULT '{}',
    excluded_keywords    TEXT[] NOT NULL DEFAULT '{}',
    category             TEXT,
    price_min            NUMERIC(12, 2) CHECK (price_min >= 0),
    price_max            NUMERIC(12, 2) CHECK (price_max >= 0),
    currency             TEXT NOT NULL DEFAULT 'PLN',
    condition            TEXT NOT NULL DEFAULT 'any' CHECK (condition IN ('any', 'new', 'used')),
    city                 TEXT,
    region               TEXT,
    radius_km            INTEGER CHECK (radius_km BETWEEN 0 AND 500),
    delivery             TEXT NOT NULL DEFAULT 'any' CHECK (delivery IN ('any', 'shipping', 'pickup')),
    include_unknown      BOOLEAN NOT NULL DEFAULT TRUE,
    fuzzy                BOOLEAN NOT NULL DEFAULT FALSE,
    interval_seconds     INTEGER NOT NULL CHECK (interval_seconds >= 30),
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    paused_reason        TEXT,
    baseline_done        BOOLEAN NOT NULL DEFAULT FALSE,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_checked_at      TIMESTAMPTZ,
    last_success_at      TIMESTAMPTZ,
    next_check_at        TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (price_min IS NULL OR price_max IS NULL OR price_min <= price_max),
    CHECK (cardinality(keywords) + cardinality(alt_keywords) > 0)
);
CREATE INDEX ix_searches_due ON searches (next_check_at) WHERE is_active;
CREATE INDEX ix_searches_user ON searches (user_id);

CREATE TABLE listings (
    id                 BIGSERIAL PRIMARY KEY,
    source             TEXT NOT NULL,
    external_id        TEXT NOT NULL,
    url                TEXT NOT NULL,
    title              TEXT NOT NULL,
    description        TEXT,
    price              NUMERIC(12, 2),
    previous_price     NUMERIC(12, 2),
    currency           TEXT,
    city               TEXT,
    region             TEXT,
    condition          TEXT CHECK (condition IN ('new', 'used')),
    category           TEXT,
    delivery_available BOOLEAN,
    latitude           DOUBLE PRECISION,
    longitude          DOUBLE PRECISION,
    posted_at          TIMESTAMPTZ,
    is_test            BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_changed_at   TIMESTAMPTZ,
    UNIQUE (source, external_id)
);
CREATE INDEX ix_listings_last_seen ON listings (last_seen_at);

CREATE TABLE monitoring_runs (
    id            BIGSERIAL PRIMARY KEY,
    search_id     BIGINT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    source        TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL CHECK (status IN ('ok', 'error', 'blocked', 'disabled', 'skipped')),
    fetched       INTEGER NOT NULL DEFAULT 0,
    new_listings  INTEGER NOT NULL DEFAULT 0,
    matched       INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);
CREATE INDEX ix_runs_search ON monitoring_runs (search_id, started_at DESC);
CREATE INDEX ix_runs_started ON monitoring_runs (started_at);

CREATE TABLE match_results (
    id          BIGSERIAL PRIMARY KEY,
    search_id   BIGINT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    listing_id  BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reasons     JSONB NOT NULL DEFAULT '[]',
    matched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (search_id, listing_id)
);

CREATE TABLE notifications (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    listing_id          BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    search_id           BIGINT REFERENCES searches(id) ON DELETE SET NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('new', 'price_drop')),
    dedup_key           TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'limited', 'cancelled')),
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at          TIMESTAMPTZ,
    last_error          TEXT,
    price_at_notify     NUMERIC(12, 2),
    telegram_message_id BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at             TIMESTAMPTZ,
    -- One notification per user per listing event, even if several searches match.
    UNIQUE (user_id, dedup_key)
);
CREATE INDEX ix_notifications_queue ON notifications (next_attempt_at) WHERE status IN ('pending', 'sending');
CREATE INDEX ix_notifications_user_sent ON notifications (user_id, sent_at);

CREATE TABLE payment_events (
    id                 BIGSERIAL PRIMARY KEY,
    provider           TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    external_event_id  TEXT NOT NULL,
    payment_id         BIGINT REFERENCES payments(id) ON DELETE SET NULL,
    payload            JSONB NOT NULL DEFAULT '{}',
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at       TIMESTAMPTZ,
    error              TEXT,
    -- Idempotency: the same provider event is processed once.
    UNIQUE (provider, external_event_id)
);

CREATE TABLE refunds (
    id                 BIGSERIAL PRIMARY KEY,
    payment_id         BIGINT NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
    amount             NUMERIC(18, 8) NOT NULL,
    reason             TEXT,
    status             TEXT NOT NULL CHECK (status IN ('requested', 'completed', 'failed')),
    admin_telegram_id  BIGINT,
    error              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_refunds_completed ON refunds (payment_id) WHERE status = 'completed';

CREATE TABLE source_states (
    source          TEXT PRIMARY KEY,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    status          TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'degraded', 'blocked', 'disabled')),
    disabled_reason TEXT,
    blocked_until   TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error_at   TIMESTAMPTZ,
    last_error      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE admin_audit_log (
    id                 BIGSERIAL PRIMARY KEY,
    admin_telegram_id  BIGINT NOT NULL,
    action             TEXT NOT NULL,
    target             TEXT,
    details            JSONB NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_created ON admin_audit_log (created_at DESC);
