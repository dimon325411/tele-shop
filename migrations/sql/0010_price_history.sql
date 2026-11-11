CREATE TABLE listing_price_history (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    currency TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('baseline', 'initial', 'change'))
);
CREATE INDEX ix_price_history_listing_time ON listing_price_history (listing_id, observed_at DESC);
CREATE INDEX ix_price_history_time ON listing_price_history (observed_at DESC);
