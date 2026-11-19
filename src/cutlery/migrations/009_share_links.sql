-- Links made from the instance's share key (share.py) can be shown again; older ones (only a hash) cannot.
ALTER TABLE shares ADD COLUMN derived INTEGER NOT NULL DEFAULT 0;
