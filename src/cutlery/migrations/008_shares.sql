-- Links that share one project with a guest (a friend who reviews): only a hash of the token is kept.
CREATE TABLE shares (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    created     TEXT NOT NULL,
    expires     TEXT,
    revoked     TEXT,
    last_used   TEXT
);
CREATE INDEX shares_project ON shares (project_id);
