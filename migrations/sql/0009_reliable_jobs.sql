ALTER TABLE notifications ADD COLUMN payload JSONB NOT NULL DEFAULT '{}';
ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check CHECK (kind IN ('new', 'price_drop', 'free'));
CREATE TABLE job_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
