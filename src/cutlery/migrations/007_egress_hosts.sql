-- Domains the user named in the briefing, the notes for a cut, review comments (per project) or the preferences
-- (project_id '' = all projects): the agent may reach them and their subdomains (egress.py).
CREATE TABLE egress_domains (
    project_id  TEXT NOT NULL,
    domain      TEXT NOT NULL,
    source      TEXT NOT NULL,
    added       TEXT NOT NULL,
    PRIMARY KEY (project_id, domain, source)
);

-- A Claude job was refused a domain: the user is asked whether to allow it (asked → allowed / refused). A refused
-- domain is not asked for again in the project.
CREATE TABLE egress_requests (
    project_id  TEXT NOT NULL,
    domain      TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    state       TEXT NOT NULL,
    asked       TEXT NOT NULL,
    answered    TEXT,
    PRIMARY KEY (project_id, domain)
);
