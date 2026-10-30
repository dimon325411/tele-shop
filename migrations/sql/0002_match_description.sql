-- Keywords are matched against the title only, unless the search enables description matching.
ALTER TABLE searches ADD COLUMN match_description BOOLEAN NOT NULL DEFAULT FALSE;
