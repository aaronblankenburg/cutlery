"""Projects and clips: creation, registration of clips by sidecar, the manifest, deletion."""
from __future__ import annotations

import json
import logging
import secrets
import shutil

from . import sidecar as sc_mod
from .db import Database, now
from .storage import ProjectPaths, write_atomic

log = logging.getLogger("cutlery.projects")

TELEMETRY = {"gpmd": (".gpmd", 256 * 1024 * 1024), "srt": (".srt", 50 * 1024 * 1024)}


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


class Projects:
    def __init__(self, cfg, db: Database):
        self.cfg, self.db = cfg, db

    def paths(self, project_id: str) -> ProjectPaths:
        return ProjectPaths(self.cfg, project_id)

    # ------------------------------------------------------------ projects

    def create(self, name: str) -> dict:
        name = " ".join((name or "").split())[:120]
        if not name:
            raise ValueError("name is empty")
        pid = secrets.token_hex(5)
        self.paths(pid).create()
        t = now()
        with self.db.tx() as con:
            con.execute("INSERT INTO projects (id, name, created, updated) VALUES (?,?,?,?)", (pid, name, t, t))
        return self.get(pid)

    def get(self, project_id: str) -> dict:
        p = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not p:
            raise NotFound(project_id)
        return p

    def list(self) -> list[dict]:
        return self.db.all("""
            SELECT p.*, COUNT(c.id) AS clips,
                   SUM(c.state='uploaded') AS uploaded, SUM(c.state IN ('unsupported','failed')) AS problems,
                   SUM(COALESCE(c.duration,0)) AS duration
            FROM projects p LEFT JOIN clips c ON c.project_id=p.id
            GROUP BY p.id ORDER BY p.updated DESC""")

    def delete(self, project_id: str) -> None:
        self.get(project_id)
        uploads = self.db.all("SELECT id FROM uploads WHERE project_id=?", (project_id,))
        with self.db.tx() as con:
            con.execute("DELETE FROM projects WHERE id=?", (project_id,))
        for u in uploads:
            (self.cfg.work_dir / "uploads" / u["id"]).unlink(missing_ok=True)
        self.paths(project_id).remove()

    def touch(self, project_id: str) -> None:
        with self.db.tx() as con:
            con.execute("UPDATE projects SET updated=? WHERE id=?", (now(), project_id))

    # ------------------------------------------------------------ clips

    def clips(self, project_id: str) -> list[dict]:
        return self.db.all("SELECT * FROM clips WHERE project_id=? ORDER BY rel_path", (project_id,))

    def clip(self, project_id: str, clip_id: str) -> dict:
        c = self.db.one("SELECT * FROM clips WHERE project_id=? AND id=?", (project_id, clip_id))
        if not c:
            raise NotFound(clip_id)
        return c

    def sidecar(self, project_id: str, clip: dict) -> dict:
        path = self.paths(project_id).sidecar(clip["rel_path"])
        return json.loads(path.read_text(encoding="utf-8"))

    def register(self, project_id: str, raw) -> dict:
        """Store a clip's sidecar. The same fingerprint again returns the existing clip (resume after reload).
        A different file at a known rel_path replaces the old clip (the original was changed)."""
        self.get(project_id)
        sc = sc_mod.validate(raw)
        fp = sc_mod.fingerprint_key(sc["fingerprint"])
        paths = self.paths(project_id)
        t = now()
        state = "unsupported" if sc.get("unsupported") else "registered"
        reason = sc["unsupported"]["reason"] if sc.get("unsupported") else None
        replaced = None
        with self.db.tx() as con:
            row = con.execute("SELECT * FROM clips WHERE project_id=? AND fingerprint=?", (project_id, fp)).fetchone()
            if row and row["rel_path"] != sc["rel_path"]:
                # same file seen under another name (renamed/moved before upload finished): follow the new name
                self._move_files(paths, row["rel_path"], sc["rel_path"])
                con.execute("UPDATE clips SET rel_path=?, updated=? WHERE project_id=? AND id=?",
                            (sc["rel_path"], t, project_id, row["id"]))
            if row:
                clip_id = row["id"]
                if row["state"] in ("unsupported", "failed") and state == "registered":
                    con.execute("UPDATE clips SET state='registered', reason=NULL, updated=? "
                                "WHERE project_id=? AND id=?", (t, project_id, clip_id))
            else:
                old = con.execute("SELECT * FROM clips WHERE project_id=? AND rel_path=?",
                                  (project_id, sc["rel_path"])).fetchone()
                if old:
                    replaced = dict(old)
                    con.execute("DELETE FROM clips WHERE project_id=? AND id=?", (project_id, old["id"]))
                    con.execute("DELETE FROM uploads WHERE project_id=? AND clip_id=?", (project_id, old["id"]))
                clip_id = self._next_id(con, project_id)
                con.execute("""INSERT INTO clips (project_id, id, fingerprint, rel_path, size, duration, state, reason,
                               created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (project_id, clip_id, fp, sc["rel_path"], sc["size"], sc.get("duration"), state, reason,
                             t, t))
            con.execute("UPDATE projects SET updated=? WHERE id=?", (t, project_id))
        if replaced:
            paths.standin(replaced["rel_path"]).unlink(missing_ok=True)
            log.info("project %s: %s changed on disk, replaced clip %s", project_id, sc["rel_path"], replaced["id"])
        sc["clip_id"] = clip_id
        write_atomic(paths.sidecar(sc["rel_path"]), json.dumps(sc, ensure_ascii=False, indent=1).encode())
        self.write_manifest(project_id)
        return self.status(project_id, clip_id)

    @staticmethod
    def _next_id(con, project_id):
        n = con.execute("SELECT next_clip FROM projects WHERE id=?", (project_id,)).fetchone()[0]
        con.execute("UPDATE projects SET next_clip=? WHERE id=?", (n + 1, project_id))
        return f"c{n:04d}"

    @staticmethod
    def _move_files(paths: ProjectPaths, old: str, new: str):
        for src, dst in [(paths.standin(old), paths.standin(new))] + [
                (paths.sidecar(old, s), paths.sidecar(new, s)) for s in (".json", ".gpmd", ".srt")]:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src, dst)

    def status(self, project_id: str, clip_id: str) -> dict:
        c = self.clip(project_id, clip_id)
        sc = self.sidecar(project_id, c)
        tel = sc.get("telemetry") or {}
        return {"id": c["id"], "rel_path": c["rel_path"], "state": c["state"], "reason": c["reason"],
                "need": {"standin": c["state"] == "registered",
                         "gpmd": bool(tel.get("gpmd")) and not c["gpmd"],
                         "srt": bool(tel.get("srt")) and not c["srt"]}}

    def write_manifest(self, project_id: str) -> None:
        paths = self.paths(project_id)
        entries = []
        for c in self.clips(project_id):
            try:
                entries.append(sc_mod.manifest_entry(c["id"], self.sidecar(project_id, c)))
            except (OSError, ValueError):
                continue
        data = {"format": "cutlery.manifest/1", "project": project_id, "clips": entries}
        write_atomic(paths.manifest, json.dumps(data, ensure_ascii=False, indent=1).encode())

    def store_telemetry(self, project_id: str, clip_id: str, kind: str, data: bytes) -> None:
        suffix, limit = TELEMETRY[kind]
        if len(data) > limit:
            raise ValueError(f"{kind} file is too large")
        if kind == "srt":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("the SRT file is not UTF-8 text")
        c = self.clip(project_id, clip_id)
        write_atomic(self.paths(project_id).sidecar(c["rel_path"], suffix), data)
        with self.db.tx() as con:
            con.execute(f"UPDATE clips SET {kind}=1, updated=? WHERE project_id=? AND id=?", (now(), project_id, clip_id))

    def mark(self, project_id: str, clip_id: str, state: str, reason: str | None = None,
             standin_size: int | None = None) -> None:
        with self.db.tx() as con:
            con.execute("UPDATE clips SET state=?, reason=?, standin_size=COALESCE(?, standin_size), updated=? "
                        "WHERE project_id=? AND id=?", (state, reason, standin_size, now(), project_id, clip_id))
