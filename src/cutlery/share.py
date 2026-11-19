"""Sharing a project with a guest: a friend who was there watches the cuts, comments on them and looks through the
footage, without the instance's login.

The owner makes a link per person (/projects/<id>/share): https://…/s/<id>.<secret>. Opening it sets a cookie
(HttpOnly, SameSite=Lax) holding the same token and goes to the review; from then on every request with the cookie and
without Basic Auth is the guest's. Only a SHA-256 hash of the secret is stored (the secret is 32 random bytes, so a
plain hash is enough); a link can expire and be revoked at once.

What a guest may do is a fixed list of paths of the shared project (GUEST_PATHS): the review (cuts, comments: write,
change and delete their own; the person is the guest's name), the material page (footage, transcripts, tags) and
suggestions there (favourites, rejects, tags into suggestions.json, which the owner takes over or not – see
material.py). Everything else – other projects, settings, uploads, deleting, the briefing, starting cuts, the
export, the Claude and Telegram tokens – is refused, also everything added later that is not on the list.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets

from .db import Database, now

COOKIE = "cutlery_guest"
NAME_MAX = 40
TOKEN_RE = re.compile(r"^([a-f0-9]{12})\.([A-Za-z0-9_-]{20,64})$")
EXPIRY_DAYS = (0, 7, 30, 90)              # 0: does not expire
USED_EVERY_S = 300                        # last_used is written at most this often

_P = r"/projects/(?P<pid>[a-f0-9]{10})"
_A = r"/api/projects/(?P<pid>[a-f0-9]{10})"
# (methods, path) a guest may use; the project in the path must be the shared one
GUEST_PATHS = [(frozenset(m.split()), re.compile(p + r"$")) for m, p in (
    ("GET HEAD", _P + r"/review"),
    ("GET HEAD", _P + r"/review/app/?(?:[A-Za-z0-9_.-]+)?"),
    # cutter's frontend in the frame calls its API and media relative to the page: /review/app/api/…
    ("GET HEAD", _P + r"/review(?:/app)?/api/(?:cuts|entries|status)"),
    ("POST", _P + r"/review(?:/app)?/api/entries"),
    ("PATCH DELETE", _P + r"/review(?:/app)?/api/entries/\d+"),
    ("GET HEAD", _P + r"/review(?:/app)?/media/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+"),
    ("GET HEAD", _P + r"/material"),
    ("GET HEAD", _P + r"/material/poster/[A-Za-z0-9_-]+\.jpg"),
    ("GET HEAD", _A + r"/material"),
    ("GET HEAD", _A + r"/material/[A-Za-z0-9_-]+/(?:transcript|tags)"),
    ("POST", _A + r"/selects"),
    ("PATCH DELETE", _A + r"/selects/[A-Za-z0-9_-]+"),
    ("GET HEAD", _P + r"/analysis/clip/[A-Za-z0-9_-]+\.mp4"),
    ("GET HEAD", _P + r"/analysis/(?:strips|faces)/[A-Za-z0-9_.-]+"),
)]
OPEN_PATHS = re.compile(r"^/(?:static/.+|i18n/[a-z]{2}\.json|lang/[a-z]{2})$")      # no project in them


def guest_may(method: str, path: str, project_id: str) -> bool:
    if method in ("GET", "HEAD") and OPEN_PATHS.match(path):
        return True
    for methods, rx in GUEST_PATHS:
        m = rx.match(path)
        if m and method in methods:
            return m.group("pid") == project_id
    return False


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class ShareError(ValueError):
    pass


class Shares:
    def __init__(self, db: Database):
        self.db = db

    def create(self, project_id: str, name: str, days: int = 0, taken: tuple[str, ...] = ()) -> tuple[dict, str]:
        """A new link for a guest; returns (the row without the hash, the token). The token is shown only now."""
        name = " ".join(str(name or "").split())
        if not name or len(name) > NAME_MAX:
            raise ShareError(f"name: 1 to {NAME_MAX} characters")
        if name.casefold() in {t.casefold() for t in taken}:
            raise ShareError("name: taken by the login of this instance")
        if days not in EXPIRY_DAYS:
            raise ShareError(f"days: one of {', '.join(map(str, EXPIRY_DAYS))}")
        sid, secret = secrets.token_hex(6), secrets.token_urlsafe(32)
        expires = None
        if days:
            expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self.db.tx() as con:
            con.execute("INSERT INTO shares (id, project_id, name, token_hash, created, expires) VALUES (?,?,?,?,?,?)",
                        (sid, project_id, name, _hash(secret), now(), expires))
        return self.get(project_id, sid), f"{sid}.{secret}"

    def get(self, project_id: str, sid: str) -> dict | None:
        r = self.db.one("SELECT id, project_id, name, created, expires, revoked, last_used FROM shares "
                        "WHERE project_id=? AND id=?", (project_id, sid))
        return dict(r) if r else None

    def list(self, project_id: str) -> list[dict]:
        rows = self.db.all("SELECT id, project_id, name, created, expires, revoked, last_used FROM shares "
                           "WHERE project_id=? ORDER BY created", (project_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["active"] = not d["revoked"] and not _expired(d["expires"])
            out.append(d)
        return out

    def revoke(self, project_id: str, sid: str) -> bool:
        with self.db.tx() as con:
            cur = con.execute("UPDATE shares SET revoked=? WHERE project_id=? AND id=? AND revoked IS NULL",
                              (now(), project_id, sid))
            return cur.rowcount > 0

    def check(self, token: str) -> dict | None:
        """The guest of a token: {"id", "project_id", "name"}, or None (unknown, revoked, expired)."""
        m = TOKEN_RE.match(token or "")
        if not m:
            return None
        r = self.db.one("SELECT s.id, s.project_id, s.name, s.token_hash, s.expires, s.revoked, s.last_used "
                        "FROM shares s JOIN projects p ON p.id = s.project_id WHERE s.id=?", (m.group(1),))
        if not r or r["revoked"] or _expired(r["expires"]) \
                or not secrets.compare_digest(r["token_hash"], _hash(m.group(2))):
            return None
        stamp = now()
        if not r["last_used"] or _seconds_between(r["last_used"], stamp) > USED_EVERY_S:
            with self.db.tx() as con:
                con.execute("UPDATE shares SET last_used=? WHERE id=?", (stamp, r["id"]))
        return {"id": r["id"], "project_id": r["project_id"], "name": r["name"]}


def _expired(expires) -> bool:
    if not expires:
        return False
    try:
        return dt.datetime.fromisoformat(expires) <= dt.datetime.now(dt.timezone.utc)
    except ValueError:
        return True


def _seconds_between(a: str, b: str) -> float:
    try:
        return abs((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds())
    except ValueError:
        return 1e9
