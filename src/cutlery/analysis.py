"""Incremental analysis: `cutter analyze` runs on the stand-ins that have arrived, while the upload goes on.

Every finished upload calls `request(project)`. A request waits until no further clip has arrived for `settle`
seconds, then one `cutter analyze` runs for the whole project. cutter keeps IDs by fingerprint and skips what it has
already produced, so every run only does the new clips. Requests that arrive during a run cause one more run after it.
At most `parallel` projects are analysed at the same time.

With the runner (CUTLERY_EXECUTOR=runner) the command runs in an isolated container like the Claude jobs (it reads the
user's uploads with ffmpeg); otherwise it runs as a child process of the web app.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from . import cutter_project
from .db import Database, now
from .storage import ProjectPaths

log = logging.getLogger("cutlery.analysis")


class AnalysisWorker:
    def __init__(self, cfg, db: Database, projects, *, settle: float | None = None, parallel: int | None = None,
                 command: list[str] | None = None, executor=None):
        self.cfg, self.db, self.projects = cfg, db, projects
        self.executor = executor
        self.settle = cfg.analysis_settle_s if settle is None else settle
        self.command = command or [cfg.cutter_bin, "analyze", *cfg.analysis_args]
        self.slots = threading.Semaphore(parallel or cfg.analysis_parallel)
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}        # project id -> {"last": time, "again": bool, "proc": Popen | None}
        self.closed = False

    # ------------------------------------------------------------ public

    def request(self, project_id: str) -> None:
        """Analyse the project soon (after the settle time); safe to call from any thread, as often as wanted."""
        with self.lock:
            if self.closed:
                return
            job = self.jobs.get(project_id)
            if job:
                job["last"], job["again"] = time.monotonic(), True
                return
            self.jobs[project_id] = {"last": time.monotonic(), "again": True, "proc": None, "cancel": False}
        self._state(project_id, "waiting")
        threading.Thread(target=self._loop, args=(project_id,), name=f"analysis-{project_id}", daemon=True).start()

    def stop(self, project_id: str) -> bool:
        """Stop the analysis because the user asked (it starts again with the next upload or "continue")."""
        with self.lock:
            known = project_id in self.jobs
        self.cancel(project_id)
        self._state(project_id, "stopped")
        return known

    def cancel(self, project_id: str, timeout: float = 30) -> None:
        """Stop a running or waiting analysis (before the project is deleted) and wait until it has ended."""
        with self.lock:
            job = self.jobs.get(project_id)
            if not job:
                return
            job["cancel"] = True
            proc = job["proc"]
        if proc and proc.poll() is None:
            proc.terminate()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if project_id not in self.jobs:
                    return
            time.sleep(0.05)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            ids = list(self.jobs)
        for pid in ids:
            self.cancel(pid, timeout=10)

    def resume(self) -> int:
        """After a restart: analyse every project that has uploaded clips cutter has not seen yet."""
        n = 0
        self.db_reset()
        for p in self.db.all("SELECT id FROM projects"):
            if self.pending(p["id"]):
                self.request(p["id"])
                n += 1
        return n

    def db_reset(self) -> None:
        with self.db.tx() as con:
            con.execute("UPDATE projects SET analysis_state='idle' WHERE analysis_state IN ('waiting','running')")

    def db_state_idle(self, project_id: str) -> None:
        self._state(project_id, "idle")

    def pending(self, project_id: str) -> bool:
        """True if an uploaded clip is not in clips.json yet, or a clip there has no image analysis (the last stage
        of cutter analyze): a run was interrupted, for example by an update of Cutlery."""
        paths = self.projects.paths(project_id)
        a = cutter_project.analysis_dir(paths)
        if any(not (a / "image" / f"{c['id']}.json").is_file() for c in cutter_project.clips_json(paths)):
            return True
        have = cutter_project.progress(paths)["fingerprints"]
        from .sidecar import fingerprint_key

        for c in self.projects.clips(project_id):
            if c["state"] != "uploaded":
                continue
            try:
                if fingerprint_key(self.projects.sidecar(project_id, c)["fingerprint"]) not in have:
                    return True
            except (OSError, ValueError, KeyError):
                continue
        return False

    # ------------------------------------------------------------ internals

    def _state(self, project_id, state, message=None):
        try:
            with self.db.tx() as con:
                con.execute("UPDATE projects SET analysis_state=?, analysis_message=?, analysis_updated=? WHERE id=?",
                            (state, message, now(), project_id))
        except Exception:                   # the project may be gone
            log.debug("cannot store the analysis state of %s", project_id, exc_info=True)

    def _loop(self, project_id: str) -> None:
        try:
            while True:
                with self.lock:
                    job = self.jobs[project_id]
                    quiet = time.monotonic() - job["last"]
                    if job["cancel"]:
                        return
                    if quiet >= self.settle:
                        job["again"] = False
                        ready = True
                    else:
                        ready = False
                if not ready:
                    time.sleep(min(0.2, max(0.01, self.settle - quiet)))
                    continue
                with self.slots:
                    if job["cancel"]:
                        return
                    self._run(project_id, job)
                with self.lock:
                    if job["cancel"] or not job["again"]:
                        return
                self._state(project_id, "waiting")
        finally:
            with self.lock:
                self.jobs.pop(project_id, None)

    def _run_isolated(self, project_id, paths, job, out) -> int:
        """cutter analyze in a container of the runner; its output is appended to the project's analysis log."""
        import secrets as _secrets
        jid = _secrets.token_hex(8)
        d = paths.work / "jobs" / jid
        d.mkdir(parents=True, exist_ok=True)
        stdout, stderr = d / "analysis.out", d / "analysis.err"
        spec = {"job": jid, "project": project_id, "kind": "analysis", "argv": ["cutter", *self.command[1:]],
                "cwd": str(paths.data), "stdout": str(stdout), "stderr": str(stderr),
                "env": {"CUTTER_PROJECT": str(paths.data), "TMPDIR": str(paths.work / "tmp")}}
        try:
            self.executor.start(spec)
            while True:
                code = self.executor.poll(jid)
                if code is not None:
                    break
                if job["cancel"]:
                    self.executor.stop(jid)
                    return -15
                time.sleep(1)
        finally:
            for f in (stdout, stderr):
                if f.exists():
                    out.write(f.read_bytes())
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        return code

    def _run(self, project_id: str, job: dict) -> None:
        try:
            project = self.projects.get(project_id)
        except LookupError:
            return
        paths: ProjectPaths = self.projects.paths(project_id)
        cutter_project.write(paths, project["name"])
        cutter_project.analysis_dir(paths).mkdir(parents=True, exist_ok=True)
        logfile = paths.work / "analysis.log"
        env = {**os.environ, "CUTTER_PROJECT": str(paths.data), "TMPDIR": str(paths.work / "tmp")}
        self._state(project_id, "running")
        log.info("project %s: analysis starts", project_id)
        started = time.monotonic()
        try:
            with open(logfile, "ab") as out:
                out.write(f"\n=== {now()} {' '.join(self.command)}\n".encode())
                out.flush()
                if self.executor is not None:
                    code = self._run_isolated(project_id, paths, job, out)
                else:
                    proc = subprocess.Popen(self.command, cwd=paths.data, env=env, stdout=out, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL)
                    with self.lock:
                        job["proc"] = proc
                    code = proc.wait()
        except OSError as e:
            self._state(project_id, "failed", f"cannot run {self.command[0]}: {e.strerror}")
            log.error("project %s: cannot run %s: %s", project_id, self.command[0], e)
            return
        finally:
            with self.lock:
                job["proc"] = None
        if job["cancel"]:
            return
        if code == 0:
            self._state(project_id, "idle")
            log.info("project %s: analysis done in %.0f s", project_id, time.monotonic() - started)
        else:
            tail = _tail(logfile)
            self._state(project_id, "failed", tail)
            log.error("project %s: analysis failed (exit %s): %s", project_id, code, tail)


def _tail(path, lines: int = 8, limit: int = 1500) -> str:
    try:
        data = path.read_bytes()[-8192:].decode("utf-8", "replace")
    except OSError:
        return ""
    text = "\n".join(line for line in data.splitlines()[-lines:] if line.strip())
    return text[-limit:]
