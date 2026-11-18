"""The review of a project: cutter's review app (frontend, API and storage), behind Cutlery's login, with a lock per cut.

The API is cutter's own (cutter.review_app.server.Handler.api: /api/cuts, /api/entries, /api/status, /api/export)
and so is the storage (feedback.sqlite in the data tree, the rendered cuts in the work tree). A small adapter calls
cutter's handler method with the request; nothing is copied. Cutlery adds:

- the lock of a cut: "Review done → next cut" freezes its comments (the next cut answers them); writes to a locked
  cut are refused with 409;
- the person of a comment is the logged-in user (one user per instance);
- media (review.mp4 with range requests, thumbnails, poster, previews of alternatives) are served by Cutlery;
- cutter's frontend (static/ of cutter.review_app) is served unchanged for a frame on the review page; the host's
  settings (reviewer = the logged-in user, language, no "send the feedback" box, embedded layout) go into the `app`
  object of /api/cuts, which the frontend reads.
"""
from __future__ import annotations

import io
import json
import pathlib
import threading

from cutter.review_app import server as rs

from . import cutter_project, egress
from .db import now

MEDIA = {".mp4", ".jpg", ".png", ".vtt", ".json"}
APP_DIR = pathlib.Path(rs.STATIC_DIR)
APP_TYPES = {".html", ".js", ".css", ".svg", ".png", ".woff2", ".json"}


def app_file(name: str) -> pathlib.Path | None:
    """A file of cutter's review frontend, or None."""
    if not rs.FILE_RE.match(name) or pathlib.Path(name).suffix.lower() not in APP_TYPES:
        return None
    f = APP_DIR / name
    return f if f.is_file() else None


class _Server:
    def __init__(self, store, cuts, app_config):
        self.store, self.cuts, self.app_config = store, cuts, app_config


class _Call(rs.Handler):
    """cutter's request handler without a socket: the request comes in, the response is kept."""

    def __init__(self, server, method, body: bytes, length):     # noqa: deliberately not calling the base __init__
        self.server, self.command = server, method
        self.headers = {"Content-Length": str(length)}
        self.rfile = io.BytesIO(body)
        self.result = None

    def send_json(self, obj, status=200):
        self.result = (status, "application/json", json.dumps(obj, ensure_ascii=False).encode())

    def send_text(self, status, text, ctype="text/plain; charset=utf-8", extra=()):
        self.result = (status, ctype, text.encode())


class Reviews:
    def __init__(self, cfg, db, projects):
        self.cfg, self.db, self.projects = cfg, db, projects
        self.lock = threading.Lock()
        self.servers: dict[str, _Server] = {}

    def paths(self, project_id):
        paths = self.projects.paths(project_id)
        data = paths.data / ".cutter" / "review" / "data"            # cutter's default: comments with the project
        media = cutter_project.review_media_dir(paths)                 # renders in the work tree
        return data, media

    def server(self, project_id) -> _Server:
        with self.lock:
            srv = self.servers.get(project_id)
            if srv is None:
                data, media = self.paths(project_id)
                data.mkdir(parents=True, exist_ok=True)
                (media / "cuts").mkdir(parents=True, exist_ok=True)
                cuts = rs.Cuts(str(media))
                cuts.folders.append(str(data / "cuts"))      # cutter before 0.3.1 renders next to the comments
                srv = _Server(rs.Store(str(data / "feedback.sqlite")), cuts, rs.AppConfig(str(data / "project.json")))
                self.servers[project_id] = srv
            return srv

    def forget(self, project_id):
        with self.lock:
            srv = self.servers.pop(project_id, None)
        if srv:
            srv.store.db.close()

    # ------------------------------------------------------------ locks

    def locked(self, project_id) -> set[str]:
        return {r["cut"] for r in self.db.all("SELECT cut FROM review_locks WHERE project_id=?", (project_id,))}

    def lock_cut(self, project_id, cut) -> None:
        with self.db.tx() as con:
            con.execute("INSERT OR IGNORE INTO review_locks (project_id, cut, locked) VALUES (?,?,?)",
                        (project_id, cut, now()))

    def unlock_cut(self, project_id, cut) -> None:
        with self.db.tx() as con:
            con.execute("DELETE FROM review_locks WHERE project_id=? AND cut=?", (project_id, cut))

    # ------------------------------------------------------------ API

    def api(self, project_id, method, path, query: str, body: bytes, user: str, app: dict | None = None):
        srv = self.server(project_id)
        if method in ("POST", "PATCH", "DELETE"):
            refused = self._check_write(project_id, srv, method, path, body, user)
            if refused:
                return refused
            if method in ("POST", "PATCH") and (path == "/api/entries" or path.startswith("/api/entries/")):
                body = self._with_person(body, user, method)
                try:
                    egress.record(self.db, project_id, "comment", str(json.loads(body).get("text") or ""))
                except (ValueError, AttributeError):
                    pass
            if method == "DELETE":
                from urllib.parse import quote
                query = f"person={quote(user)}"
        call = _Call(srv, method, body, len(body))
        from urllib.parse import parse_qs
        call.api(path, parse_qs(query or ""), user)
        status, ctype, data = call.result or (500, "application/json", b'{"error": "no response"}')
        if path == "/api/cuts" and method == "GET" and status == 200:
            d = json.loads(data)
            locked = self.locked(project_id)
            for c in d.get("cuts", []):
                c["locked"] = c["id"] in locked
            d["login"] = user
            if app:
                d["app"] = {**(d.get("app") or {}), **app}
            data = json.dumps(d, ensure_ascii=False).encode()
        return status, ctype, data

    @staticmethod
    def _with_person(body, user, method):
        try:
            d = json.loads(body or b"{}")
        except ValueError:
            return body
        if isinstance(d, dict):
            d["person"] = user
        return json.dumps(d).encode()

    def _check_write(self, project_id, srv, method, path, body, user):
        locked = self.locked(project_id)
        if not locked:
            return None
        cuts = set()
        try:
            d = json.loads(body or b"{}") if body else {}
        except ValueError:
            d = {}
        if path == "/api/entries" and method == "POST":
            cuts.add(d.get("cut"))
        elif path.startswith("/api/entries/"):
            try:
                e = srv.store.one(int(path.rsplit("/", 1)[1]))
            except ValueError:
                e = None
            if e:
                cuts.add(e["cut"])
        elif path == "/api/status":
            for i in d.get("ids") or []:
                e = srv.store.one(i) if isinstance(i, int) else None
                if e:
                    cuts.add(e["cut"])
        if cuts & locked:
            return 409, "application/json", json.dumps({"error": "this cut's review is done; its comments are locked",
                                                        "locked": True}).encode()
        return None

    def media(self, project_id, cut, name) -> pathlib.Path | None:
        if not rs.CUT_RE.match(cut) or not rs.FILE_RE.match(name) or pathlib.Path(name).suffix.lower() not in MEDIA:
            return None
        folder = self.server(project_id).cuts.folder(cut)
        if not folder:
            return None
        p = pathlib.Path(folder) / name
        return p if p.is_file() else None

    def summary(self, project_id) -> list[dict]:
        """The published cuts with their comment counts (for the status page and the project list)."""
        status, _, data = self.api(project_id, "GET", "/api/cuts", "", b"", "-")
        return json.loads(data).get("cuts", []) if status == 200 else []
