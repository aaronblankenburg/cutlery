-- Claude Code jobs: one cut per job, at most one active job per project.
CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                 -- draft (first cut) or next (next cut from the review)
    cut         TEXT NOT NULL,                 -- the cut this job makes, e.g. cut1
    state       TEXT NOT NULL,                 -- queued, running, paused, failed, done, cancelled
    model       TEXT NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 1,    -- 2, 3 … after resuming
    reason      TEXT,                          -- why paused or failed: usage_limit, auth, max_turns, timeout, error, crash
    message     TEXT,                          -- the last words of the agent or the error
    resume_after TEXT,                         -- when a paused job can probably continue (usage limit reset)
    session_id  TEXT,
    cost_usd    REAL,
    turns       INTEGER,
    created     TEXT NOT NULL,
    started     TEXT,
    ended       TEXT
);
CREATE INDEX jobs_project ON jobs(project_id, created);
CREATE INDEX jobs_state ON jobs(state, created);
