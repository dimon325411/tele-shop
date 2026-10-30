-- Deal area: links only from the owner's city + radius (statistics stay nationwide).
ALTER TABLE users ADD COLUMN deal_city TEXT;
ALTER TABLE users ADD COLUMN deal_radius_km INTEGER;
