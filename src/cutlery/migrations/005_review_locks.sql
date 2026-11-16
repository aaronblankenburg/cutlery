-- "Review done → next cut" locks the comments of a cut: the next cut answers them.
CREATE TABLE review_locks (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    cut         TEXT NOT NULL,
    locked      TEXT NOT NULL,
    PRIMARY KEY (project_id, cut)
);
