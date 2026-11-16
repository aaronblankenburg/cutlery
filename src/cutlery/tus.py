"""Resumable upload of the 360p stand-ins with the tus protocol 1.0.0 (core, creation, termination, expiration).

The browser asks for an upload of one clip (Upload-Metadata: project, clip). Bytes go to WORK_DIR/uploads/<id>;
when the last byte has arrived, the file is checked with ffprobe against the clip's sidecar (cutter's proxy
contract: H.264, same length) and moved to the project's media folder.
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import os
import secrets
import subprocess

import anyio
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from . import sidecar as sc_mod
from .db import now
from .projects import NotFound

log = logging.getLogger("cutlery.upload")

VERSION = "1.0.0"
MAX_SIZE = 8 * 1024 ** 3
MAX_STANDIN_SIDE = 640


def _headers(extra=None):
    h = {"Tus-Resumable": VERSION, "Cache-Control": "no-store"}
    h.update(extra or {})
    return h


def _error(status, text):
    return Response(text + "\n", status_code=status, headers=_headers(), media_type="text/plain")


def parse_metadata(header: str) -> dict:
    out = {}
    for pair in (header or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, value = pair.partition(" ")
        try:
            out[key] = base64.b64decode(value).decode("utf-8") if value else ""
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f"Upload-Metadata: '{key}' is not valid base64")
    return out


def ffprobe(path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-show_format", "-of", "json",
                        str(path)], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise ValueError("ffprobe cannot read the file: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    return json.loads(r.stdout)


def check_standin(info: dict, sidecar: dict) -> str | None:
    """Problems of an uploaded stand-in, or None."""
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video or video.get("codec_name") != "h264":
        return "the 360p copy has no H.264 video"
    if max(int(video.get("width", 0)), int(video.get("height", 0))) > MAX_STANDIN_SIDE:
        return "the 360p copy is larger than 640 px"
    if sidecar.get("audio") and not audio:
        return "the 360p copy has no sound, the original has"
    if audio and audio.get("codec_name") != "aac":
        return "the sound of the 360p copy is not AAC"
    duration = float(video.get("duration") or info.get("format", {}).get("duration") or 0)
    frames = int(video["nb_read_packets"]) if video.get("nb_read_packets") else None
    return sc_mod.standin_matches(sidecar, duration, frames)


class TusApp:
    def __init__(self, cfg, db, projects, base="/tus/", on_stored=None):
        self.cfg, self.db, self.projects, self.base = cfg, db, projects, base
        self.on_stored = on_stored             # called with the project id after a stand-in has been stored
        self.dir = cfg.work_dir / "uploads"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.locks: dict[str, asyncio.Lock] = {}

    def routes(self):
        return [Route(self.base, self.collection, methods=["OPTIONS", "POST"]),
                Route(self.base + "{uid}", self.resource, methods=["OPTIONS", "HEAD", "PATCH", "DELETE"])]

    def _options(self):
        return Response(status_code=204, headers={
            "Tus-Resumable": VERSION, "Tus-Version": VERSION, "Tus-Max-Size": str(MAX_SIZE),
            "Tus-Extension": "creation,termination,expiration"})

    def _expires(self, row):
        return datetime.datetime.fromisoformat(row["expires"]).strftime("%a, %d %b %Y %H:%M:%S GMT")

    async def collection(self, request: Request):
        if request.method == "OPTIONS":
            return self._options()
        if request.headers.get("tus-resumable") != VERSION:
            return _error(412, "Tus-Resumable 1.0.0 is required")
        try:
            length = int(request.headers.get("upload-length", ""))
            meta = parse_metadata(request.headers.get("upload-metadata", ""))
        except ValueError as e:
            return _error(400, str(e) or "Upload-Length is required")
        if not 0 < length <= MAX_SIZE:
            return _error(413, "upload is too large")
        try:
            clip = self.projects.clip(meta.get("project", ""), meta.get("clip", ""))
        except (NotFound, ValueError):
            return _error(404, "unknown project or clip")
        if clip["state"] not in ("registered", "failed"):
            return _error(409, f"clip {clip['id']} does not need an upload (state {clip['state']})")
        uid = secrets.token_hex(16)
        expires = (datetime.datetime.now(datetime.timezone.utc)
                   + datetime.timedelta(hours=self.cfg.upload_expiry_h)).isoformat(timespec="seconds")
        (self.dir / uid).touch()
        with self.db.tx() as con:
            con.execute("DELETE FROM uploads WHERE project_id=? AND clip_id=?", (clip["project_id"], clip["id"]))
            con.execute("INSERT INTO uploads (id, project_id, clip_id, length, received, created, expires) "
                        "VALUES (?,?,?,?,0,?,?)", (uid, clip["project_id"], clip["id"], length, now(), expires))
        row = self.db.one("SELECT * FROM uploads WHERE id=?", (uid,))
        return Response(status_code=201, headers=_headers({
            "Location": self.base + uid,
            "Upload-Expires": self._expires(row)}))

    def _row(self, uid):
        if not uid.isalnum():
            return None
        return self.db.one("SELECT * FROM uploads WHERE id=?", (uid,))

    async def resource(self, request: Request):
        if request.method == "OPTIONS":
            return self._options()
        if request.headers.get("tus-resumable") != VERSION:
            return _error(412, "Tus-Resumable 1.0.0 is required")
        uid = request.path_params["uid"]
        row = self._row(uid)
        if not row:
            return _error(404, "unknown upload")
        if request.method == "HEAD":
            return Response(status_code=200, headers=_headers({
                "Upload-Offset": str(row["received"]), "Upload-Length": str(row["length"]),
                "Upload-Expires": self._expires(row)}))
        if request.method == "DELETE":
            await self._discard(uid)
            return Response(status_code=204, headers=_headers())
        return await self._patch(request, uid)

    async def _patch(self, request: Request, uid: str):
        if request.headers.get("content-type") != "application/offset+octet-stream":
            return _error(415, "Content-Type must be application/offset+octet-stream")
        lock = self.locks.setdefault(uid, asyncio.Lock())
        if lock.locked():
            return _error(409, "another request is writing this upload")
        async with lock:
            row = self._row(uid)
            if not row:
                return _error(404, "unknown upload")
            try:
                offset = int(request.headers.get("upload-offset", ""))
            except ValueError:
                return _error(400, "Upload-Offset is required")
            if offset != row["received"]:
                return _error(409, f"Upload-Offset is {row['received']}, not {offset}")
            path = self.dir / uid
            received = offset
            try:
                with open(path, "r+b") as f:
                    f.truncate(offset)
                    f.seek(offset)
                    async for chunk in request.stream():
                        if received + len(chunk) > row["length"]:
                            return _error(413, "more bytes than Upload-Length")
                        await anyio.to_thread.run_sync(f.write, chunk)
                        received += len(chunk)
            except OSError as e:
                log.error("upload %s: cannot write (%s)", uid, e)
                return _error(507, "the server cannot store the upload")
            finally:
                with self.db.tx() as con:
                    con.execute("UPDATE uploads SET received=? WHERE id=?", (received, uid))
            extra = {"Upload-Offset": str(received), "Upload-Expires": self._expires(row)}
            if received == row["length"]:
                problem = await anyio.to_thread.run_sync(self._finish, row)
                if problem:
                    extra["Cutlery-Upload-Problem"] = base64.b64encode(problem.encode()).decode()
                    return Response(problem + "\n", status_code=422, headers=_headers(extra),
                                    media_type="text/plain")
            return Response(status_code=204, headers=_headers(extra))

    def _finish(self, row) -> str | None:
        """Check the complete stand-in and move it into place. Returns a problem text or None."""
        pid, cid = row["project_id"], row["clip_id"]
        src = self.dir / row["id"]
        try:
            clip = self.projects.clip(pid, cid)
            sidecar = self.projects.sidecar(pid, clip)
            problem = check_standin(ffprobe(src), sidecar)
        except (NotFound, OSError, ValueError, subprocess.TimeoutExpired) as e:
            problem = str(e) or e.__class__.__name__
            clip = None
        with self.db.tx() as con:
            con.execute("DELETE FROM uploads WHERE id=?", (row["id"],))
        if problem or clip is None:
            src.unlink(missing_ok=True)
            if clip is not None:
                self.projects.mark(pid, cid, "failed", problem)
            log.warning("project %s clip %s: upload rejected: %s", pid, cid, problem)
            return problem
        dst = self.projects.paths(pid).standin(clip["rel_path"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        self.projects.mark(pid, cid, "uploaded", None, dst.stat().st_size)
        self.projects.touch(pid)
        log.info("project %s clip %s: stand-in stored (%s)", pid, cid, clip["rel_path"])
        if self.on_stored:
            self.on_stored(pid)
        return None

    async def _discard(self, uid):
        with self.db.tx() as con:
            con.execute("DELETE FROM uploads WHERE id=?", (uid,))
        (self.dir / uid).unlink(missing_ok=True)

    def expire(self) -> int:
        """Remove uploads past their expiry date; returns how many."""
        old = self.db.all("SELECT id FROM uploads WHERE expires < ?", (now(),))
        for row in old:
            (self.dir / row["id"]).unlink(missing_ok=True)
        with self.db.tx() as con:
            con.execute("DELETE FROM uploads WHERE expires < ?", (now(),))
        return len(old)
