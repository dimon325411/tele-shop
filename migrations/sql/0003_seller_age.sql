-- Seller account age (anti-scam signal) and the user's choice to hide listings of new accounts.
ALTER TABLE listings ADD COLUMN seller_created_at TIMESTAMPTZ;
ALTER TABLE listings ADD COLUMN seller_is_business BOOLEAN;
ALTER TABLE users ADD COLUMN hide_new_sellers BOOLEAN NOT NULL DEFAULT FALSE;
