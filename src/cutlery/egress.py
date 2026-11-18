"""Hosts the user named: the agent of a project may reach them (and their subdomains) besides the fixed allowlist.

Only what the user typed into Cutlery counts, taken when it is saved: the briefing, the notes for a cut, the review
comments (per project) and the preferences (all projects). Files in the project folder are not read for this: the
agent can write them, and must not open the network for itself.

A web address (https://tile.example.org/x) or a bare domain with a common top-level domain (example.org) is
reduced to its registrable domain (example.org, example.co.uk); the rules are that domain and "*.<domain>".
"""
from __future__ import annotations

import ipaddress
import re

from .db import now

URL_RE = re.compile(r"\bhttps?://([^\s/:?#\"'<>()\[\]]+)", re.I)
# bare domains only with a top-level domain that is not also a common file extension (briefing.md, edit.json …)
TLDS = {"com", "org", "net", "info", "io", "eu", "de", "at", "ch", "uk", "fr", "it", "es", "nl", "be", "dk", "se",
        "no", "fi", "pt", "ie", "us", "ca", "au", "nz", "na", "za", "is", "jp", "edu", "gov", "app", "dev", "fm",
        "tv", "me", "co", "earth", "travel", "music", "art", "photo", "photos", "maps", "xyz", "online", "site",
        "website", "cloud", "world", "media", "audio", "video", "film", "studio"}
BARE_RE = re.compile(r"(?<![\w@./-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,24}))(?![\w-]|\.[a-z0-9])", re.I)
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "ne", "or", "go", "gv"}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
MAX_DOMAINS = 100


def registrable(host: str) -> str | None:
    """example.org for tile.example.org, example.co.uk for www.example.co.uk; None for IPs and single names."""
    host = host.lower().strip().rstrip(".")
    if host.startswith("*."):
        host = host[2:]
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2 or not DOMAIN_RE.match(host):
        return None
    if len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        n = 3                                          # example.co.uk
        if len(labels) < n:
            return None                                # a public suffix itself (co.uk)
    else:
        n = 2
    return ".".join(labels[-n:])


def domains(text: str) -> set[str]:
    """The registrable domains named in a text."""
    out = set()
    for m in URL_RE.finditer(text or ""):
        host = m.group(1).rsplit("@", 1)[-1]           # user:pass@host
        d = registrable(host)
        if d:
            out.add(d)
    for m in BARE_RE.finditer(text or ""):
        if m.group(2).lower() in TLDS:
            d = registrable(m.group(1))
            if d:
                out.add(d)
    return out


def rules(domains_) -> list[str]:
    """Allowlist rules for the domains: each domain and its subdomains."""
    return [r for d in sorted(domains_) for r in (d, f"*.{d}")]


def valid_rule(rule: str) -> bool:
    return isinstance(rule, str) and DOMAIN_RE.match(rule[2:] if rule.startswith("*.") else rule) is not None


# ---------------------------------------------------------------- what the user named (database)

ALL = ""                                               # project_id of the preferences: every project


def record(db, project_id: str, source: str, text: str, replace: bool = False) -> set[str]:
    """Remember the domains of a text the user saved. replace: the text replaces the earlier one of this source
    (briefing, preferences); otherwise the domains are added (notes, comments)."""
    found = domains(text)
    with db.tx() as con:
        if replace:
            con.execute("DELETE FROM egress_domains WHERE project_id=? AND source=?", (project_id, source))
        for d in found:
            con.execute("INSERT OR IGNORE INTO egress_domains (project_id, domain, source, added) VALUES (?,?,?,?)",
                        (project_id, d, source, now()))
    return found


def for_project(db, project_id: str) -> list[str]:
    """The domains the agent of a project may reach besides the fixed allowlist."""
    rows = db.all("SELECT DISTINCT domain FROM egress_domains WHERE project_id IN (?, ?) ORDER BY domain LIMIT ?",
                  (project_id, ALL, MAX_DOMAINS))
    return [r["domain"] for r in rows]


def grant(db, project_id: str, domain: str) -> None:
    """The user allowed a domain the agent asked for (or named it)."""
    with db.tx() as con:
        con.execute("INSERT OR IGNORE INTO egress_domains (project_id, domain, source, added) VALUES (?,?,?,?)",
                    (project_id, domain, "granted", now()))
        con.execute("UPDATE egress_requests SET state='allowed', answered=? WHERE project_id=? AND domain=?",
                    (now(), project_id, domain))


def refuse(db, project_id: str, domain: str) -> None:
    with db.tx() as con:
        con.execute("UPDATE egress_requests SET state='refused', answered=? WHERE project_id=? AND domain=?",
                    (now(), project_id, domain))


def ask(db, project_id: str, job_id: str, domain: str) -> bool:
    """A job was refused a domain: True if the user is to be asked now (not asked or answered before)."""
    with db.tx() as con:
        cur = con.execute("INSERT OR IGNORE INTO egress_requests (project_id, domain, job_id, state, asked) "
                          "VALUES (?,?,?,?,?)", (project_id, domain, job_id, "asked", now()))
        return cur.rowcount == 1


def requests(db, project_id: str) -> list[dict]:
    return db.all("SELECT domain, job_id, state, asked, answered FROM egress_requests WHERE project_id=? "
                  "ORDER BY asked DESC", (project_id,))


def overview(db, project_id: str) -> dict:
    """What the agent of a project may reach besides the fixed list, and what it asked for."""
    named = db.all("SELECT domain, source FROM egress_domains WHERE project_id IN (?, ?) ORDER BY domain",
                   (project_id, ALL))
    return {"allowed": named, "requests": requests(db, project_id)}
