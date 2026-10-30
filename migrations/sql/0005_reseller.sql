-- Reseller mode: deal threshold per search and daily market snapshots (price history).
ALTER TABLE searches ADD COLUMN deal_min_discount SMALLINT CHECK (deal_min_discount BETWEEN 1 AND 90);
CREATE TABLE market_snapshots (
    search_id  BIGINT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    day        DATE NOT NULL,
    variant    TEXT NOT NULL,
    count      INTEGER NOT NULL,
    median     NUMERIC(12, 2) NOT NULL,
    p25        NUMERIC(12, 2) NOT NULL,
    p75        NUMERIC(12, 2) NOT NULL,
    min_price  NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (search_id, day, variant)
);
