-- Projects and their clips. Upload state lives here so the browser can resume after a reload.
CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    next_clip   INTEGER NOT NULL DEFAULT 1,     -- clip ids are never reused, even after a clip was replaced
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);

CREATE TABLE clips (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id          TEXT NOT NULL,                 -- c0001 … within the project
    fingerprint TEXT NOT NULL,                 -- size:sha256(first MiB):sha256(last MiB)
    rel_path    TEXT NOT NULL,
    size        INTEGER NOT NULL,
    duration    REAL,
    state       TEXT NOT NULL,                 -- registered, uploaded, unsupported, failed
    reason      TEXT,                          -- why a clip is unsupported or failed
    standin_size INTEGER,
    gpmd        INTEGER NOT NULL DEFAULT 0,
    srt         INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL,
    PRIMARY KEY (project_id, id),
    UNIQUE (project_id, fingerprint)
);
CREATE INDEX clips_rel_path ON clips(project_id, rel_path);

-- Resumable uploads (tus). The file grows in WORK_DIR/uploads/<id>.
CREATE TABLE uploads (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clip_id     TEXT NOT NULL,
    length      INTEGER NOT NULL,
    received    INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL,
    expires     TEXT NOT NULL
);
