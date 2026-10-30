-- "Oddam za darmo" alerts in the deal area.
ALTER TABLE users ADD COLUMN free_alerts BOOLEAN NOT NULL DEFAULT FALSE;
