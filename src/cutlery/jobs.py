"""Claude Code jobs: one cut per job, run with `claude -p` in the project folder.

A job is queued by the user ("Cut a draft", "Review done → next cut"). The scheduler starts queued jobs while fewer
than `jobs_parallel` run, never two for one project. Each run is a fresh session: the project files are the context.
The executor runs the command (locally for development and tests, or in an isolated container through the runner,
see runner.py); its stdout (stream-json) goes to a file that the scheduler reads as it grows and turns into log
events for the UI. When the run ends, stream.outcome decides: done, paused (usage limit, authentication; resumed by
the user with a fresh session) or failed.

Files per job, in the work tree: jobs/<job>/attempt-<n>.jsonl (raw stream-json), attempt-<n>.err (stderr),
events.jsonl (log events with time, for the UI).
"""
from __future__ import annotations

import json
import logging
import pathlib
import secrets
import threading
import time

from . import cutter_project, stream
from .executor import NoCredential
from .db import Database, now

log = logging.getLogger("cutlery.jobs")

ACTIVE = ("queued", "running", "paused")
# what the agent may do: cutter and ffprobe, reading and writing in the project (data and work tree), the skill,
# searching and fetching music (the egress proxy limits the hosts, see runner.py)
ALLOWED_TOOLS = ("Bash(cutter *)", "Bash(ffprobe *)", "Read", "Write", "Edit", "Glob", "Grep", "Skill(video-editing)",
                 "WebSearch", "WebFetch", "TodoWrite")
KINDS = ("draft", "next")

PROMPTS = {
    "draft": ("/video-editing Cut the first draft of the film in this project folder as {cut}, using the video-editing skill. "
              "This is the non-interactive mode: briefing.md holds the briefing; ask nothing, decide missing points "
              "yourself and record every assumption in cuts/{cut}/decisions.json (kind \"assumption\"). The footage is "
              "analysed already (cutter analyze; clips.json, transcripts, quality.txt); run it again only for clips "
              "that are missing. Finish with the timeline, the FilmCraft export and `cutter review-data {cut}`."),
    "next": ("/video-editing Make the next cut {cut} from the review of {previous} in this project folder, using the video-editing "
             "skill (references/next-cut.md). This is the non-interactive mode: briefing.md holds the briefing; ask "
             "nothing, record every assumption in cuts/{cut}/decisions.json (kind \"assumption\"). Apply the feedback, "
             "answer every comment of {previous} in the language of the comment, and finish with the timeline, the "
             "FilmCraft export and `cutter review-data {cut}`."),
}
RESUMED = (" An earlier session of this job stopped before it was finished (attempt {attempt}). Continue from the state "
           "of the project files: check what of the steps above exists already and do the rest.")


class JobError(ValueError):
    """The job cannot be created or changed; `key` names a UI message."""

    def __init__(self, text, key="jobs.error.generic"):
        super().__init__(text)
        self.key = key


def job_dir(paths, job_id) -> pathlib.Path:
    return paths.work / "jobs" / job_id


class Jobs:
    def __init__(self, cfg, db: Database, projects, executor, notifier=None, ready=None):
        self.cfg, self.db, self.projects, self.executor = cfg, db, projects, executor
        self.notifier = notifier
        # ready(project_id) -> None if a job of the project may start now, else why not (upload, analysis)
        self.ready = ready or (lambda project_id: None if cutter_project.clips_json(projects.paths(project_id))
                               else "analysis")
        self.waiting: dict[str, str] = {}      # job id -> what it waits for (logged once)
        self.lock = threading.RLock()
        self.read_pos: dict[str, int] = {}     # job id -> bytes of the stream file already read
        self.results: dict[str, dict] = {}     # job id -> the result event of the current attempt
        self.started_at: dict[str, float] = {}
        self.stop_event = threading.Event()
        self.thread = None
        self._wake = False

    # ------------------------------------------------------------ queries

    def get(self, job_id: str) -> dict:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not j:
            raise LookupError(job_id)
        return j

    def for_project(self, project_id: str) -> list[dict]:
        return self.db.all("SELECT * FROM jobs WHERE project_id=? ORDER BY created DESC", (project_id,))

    def active(self, project_id: str) -> dict | None:
        return self.db.one(f"SELECT * FROM jobs WHERE project_id=? AND state IN ({','.join('?' * len(ACTIVE))}) "
                           "ORDER BY created DESC LIMIT 1", (project_id, *ACTIVE))

    def running_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE state='running'")["n"]

    def events(self, job_id: str, after: int = 0) -> list[dict]:
        j = self.get(job_id)
        f = job_dir(self.projects.paths(j["project_id"]), job_id) / "events.jsonl"
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for n, line in enumerate(lines[after:], after + 1):
            try:
                out.append(dict(json.loads(line), n=n))
            except ValueError:
                continue
        return out

    # ------------------------------------------------------------ user actions

    def create(self, project_id: str, kind: str) -> dict:
        if kind not in KINDS:
            raise JobError(f"unknown job kind {kind}")
        self.projects.get(project_id)
        paths = self.projects.paths(project_id)
        with self.lock:
            if self.active(project_id):
                raise JobError("a job of this project is still active", "jobs.error.active")
            cuts = cutter_project.cuts(paths)
            if kind == "draft":
                if cuts:
                    raise JobError("the project has a cut already", "jobs.error.has_cut")
                if not self.projects.clips(project_id):
                    raise JobError("there is no footage yet", "jobs.error.no_footage")
                if not (paths.data / "briefing.md").is_file():
                    raise JobError("the briefing is missing", "jobs.error.no_briefing")
                cut = "cut1"
            else:
                if not cuts:
                    raise JobError("there is no cut to review yet", "jobs.error.no_cut")
                cut = next_cut(cuts[-1])
            model = self.cfg.model_draft if kind == "draft" else self.cfg.model_next
            jid = secrets.token_hex(8)
            with self.db.tx() as con:
                con.execute("INSERT INTO jobs (id, project_id, kind, cut, state, model, created) VALUES (?,?,?,?,?,?,?)",
                            (jid, project_id, kind, cut, "queued", model, now()))
        self._event(jid, {"kind": "queued", "text": f"{kind} {cut}, model {model}"})
        log.info("project %s: job %s queued (%s %s, %s)", project_id, jid, kind, cut, model)
        self.wake()
        return self.get(jid)

    def resume(self, job_id: str) -> dict:
        with self.lock:
            j = self.get(job_id)
            if j["state"] not in ("paused", "failed"):
                raise JobError(f"job is {j['state']}", "jobs.error.not_resumable")
            other = self.active(j["project_id"])
            if other and other["id"] != job_id:
                raise JobError("another job of this project is active", "jobs.error.active")
            with self.db.tx() as con:
                con.execute("UPDATE jobs SET state='queued', attempt=attempt+1, reason=NULL, resume_after=NULL, "
                            "ended=NULL WHERE id=?", (job_id,))
        self._event(job_id, {"kind": "queued", "text": f"resumed (attempt {j['attempt'] + 1})"})
        self.wake()
        return self.get(job_id)

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            j = self.get(job_id)
            if j["state"] not in ACTIVE:
                return j
            if j["state"] == "running":
                self.executor.stop(job_id)
            self._finish(j, {"state": "cancelled", "reason": "cancelled", "message": None, "resume_after": None},
                         notify=False)
        return self.get(job_id)

    def cancel_project(self, project_id: str) -> None:
        for j in self.for_project(project_id):
            if j["state"] in ACTIVE:
                self.cancel(j["id"])

    # ------------------------------------------------------------ scheduler

    def start(self) -> None:
        self.recover()
        self.thread = threading.Thread(target=self._loop, name="jobs", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        self.wake()
        if self.thread:
            self.thread.join(5)

    def wake(self) -> None:
        self._wake = True

    def recover(self) -> None:
        """After a restart: jobs that were running either still run (runner) or were interrupted (local)."""
        for j in self.db.all("SELECT * FROM jobs WHERE state='running'"):
            if self.executor.alive(j["id"]):
                self.read_pos[j["id"]] = 0           # events are written again from the start: drop the old ones
                (job_dir(self.projects.paths(j["project_id"]), j["id"]) / "events.jsonl").unlink(missing_ok=True)
                self.started_at[j["id"]] = time.monotonic()
                log.info("job %s still runs, following it again", j["id"])
            else:
                self._finish(j, {"state": "paused", "reason": "interrupted",
                                 "message": "Cutlery was restarted while the job ran.", "resume_after": None})

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self._wake = False
            try:
                self.tick()
            except Exception:
                log.exception("job scheduler")
            for _ in range(10):
                if self._wake or self.stop_event.is_set():
                    break
                time.sleep(0.1)

    def tick(self) -> None:
        with self.lock:
            for j in self.db.all("SELECT * FROM jobs WHERE state='running'"):
                self._follow(j)
            free = self.cfg.jobs_parallel - self.running_count()
            if free <= 0:
                return
            busy = {r["project_id"] for r in self.db.all("SELECT project_id FROM jobs WHERE state='running'")}
            for j in self.db.all("SELECT * FROM jobs WHERE state='queued' ORDER BY created"):
                if free <= 0:
                    break
                if j["project_id"] in busy:
                    continue
                why = self.ready(j["project_id"])
                if why:                                  # the draft starts when upload and analysis are done
                    if self.waiting.get(j["id"]) != why:
                        self.waiting[j["id"]] = why
                        self._event(j["id"], {"kind": "waiting", "reason": why, "text": why})
                    continue
                self.waiting.pop(j["id"], None)
                self._launch(j)
                busy.add(j["project_id"])
                free -= 1

    def _stream_file(self, j) -> pathlib.Path:
        return job_dir(self.projects.paths(j["project_id"]), j["id"]) / f"attempt-{j['attempt']}.jsonl"

    def command(self, j) -> list[str]:
        cuts = cutter_project.cuts(self.projects.paths(j["project_id"]))
        previous = cuts[-1] if cuts and j["kind"] == "next" else ""
        prompt = PROMPTS[j["kind"]].format(cut=j["cut"], previous=previous)
        if j["attempt"] > 1:
            prompt += RESUMED.format(attempt=j["attempt"])
        paths = self.projects.paths(j["project_id"])
        return [self.cfg.claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--model", j["model"], "--max-turns", str(self.cfg.max_turns),
                # never ask (nobody answers): what is not allowed here is denied
                "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED_TOOLS,
                "--add-dir", str(paths.work), "--add-dir", str(pathlib.Path(self.cfg.skill_dir).parent),
                *self.cfg.claude_args]

    def _launch(self, j) -> None:
        paths = self.projects.paths(j["project_id"])
        d = job_dir(paths, j["id"])
        d.mkdir(parents=True, exist_ok=True)
        out = self._stream_file(j)
        err = out.with_suffix(".err")
        out.write_bytes(b"")
        setup_project(paths, self.cfg.skill_dir)
        config_dir = d / f"claude-{j['attempt']}"            # a fresh Claude Code configuration per session
        config_dir.mkdir(exist_ok=True)
        spec = {"job": j["id"], "project": j["project_id"], "argv": self.command(j), "cwd": str(paths.data),
                "stdout": str(out), "stderr": str(err),
                "env": {"CUTTER_PROJECT": str(paths.data), "TMPDIR": str(paths.work / "tmp"),
                        "CLAUDE_CONFIG_DIR": str(config_dir), "HOME": str(config_dir)}}
        with self.db.tx() as con:
            con.execute("UPDATE jobs SET state='running', started=?, message=NULL WHERE id=?", (now(), j["id"]))
        self.read_pos[j["id"]] = 0
        self.results.pop(j["id"], None)
        self.started_at[j["id"]] = time.monotonic()
        self._event(j["id"], {"kind": "start", "text": f"attempt {j['attempt']}, model {j['model']}"})
        try:
            self.executor.start(spec)
        except NoCredential as e:
            log.warning("job %s: paused, %s", j["id"], e)
            self._finish(self.get(j["id"]), {"state": "paused", "reason": "no_credential", "message": str(e), "resume_after": None})
            return
        except Exception as e:
            log.error("job %s: cannot start: %s", j["id"], e)
            self._finish(self.get(j["id"]), {"state": "failed", "reason": "start", "message": str(e), "resume_after": None})
            return
        log.info("job %s started (attempt %d)", j["id"], j["attempt"])

    def _read_new(self, j) -> None:
        f = self._stream_file(j)
        pos = self.read_pos.get(j["id"], 0)
        try:
            with open(f, "rb") as fh:
                fh.seek(pos)
                data = fh.read()
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:                                  # no complete line yet
            return
        complete = data[:end + 1]
        self.read_pos[j["id"]] = pos + len(complete)
        for line in complete.decode("utf-8", "replace").splitlines():
            for ev in stream.parse_line(line):
                if ev["kind"] == "result":
                    self.results[j["id"]] = ev
                if ev["kind"] == "start" and ev.get("session_id"):
                    with self.db.tx() as con:
                        con.execute("UPDATE jobs SET session_id=? WHERE id=?", (ev["session_id"], j["id"]))
                self._event(j["id"], ev)

    def _follow(self, j) -> None:
        self._read_new(j)
        code = self.executor.poll(j["id"])
        timed_out = time.monotonic() - self.started_at.get(j["id"], time.monotonic()) > self.cfg.job_timeout_s
        if code is None and not timed_out:
            return
        if code is None:
            self.executor.stop(j["id"])
            result = {"state": "failed", "reason": "timeout",
                      "message": f"stopped after {self.cfg.job_timeout_s // 3600} h", "resume_after": None}
        else:
            self._read_new(j)
            err = self._stream_file(j).with_suffix(".err")
            tail = err.read_text(encoding="utf-8", errors="replace")[-2000:] if err.exists() else ""
            result = stream.outcome(self.results.get(j["id"]), code, tail)
        self._finish(j, result)

    def _finish(self, j, result, notify=True) -> None:
        res = self.results.pop(j["id"], {}) or {}
        with self.db.tx() as con:
            con.execute("UPDATE jobs SET state=?, reason=?, message=?, resume_after=?, ended=?, "
                        "cost_usd=COALESCE(?, cost_usd), turns=COALESCE(?, turns) WHERE id=?",
                        (result["state"], result["reason"], result["message"], result["resume_after"], now(),
                         res.get("cost_usd"), res.get("turns"), j["id"]))
        self.read_pos.pop(j["id"], None)
        self.started_at.pop(j["id"], None)
        self._event(j["id"], {"kind": result["state"], "text": result["message"] or result["state"],
                              "reason": result["reason"], "resume_after": result["resume_after"]})
        log.info("job %s: %s (%s)", j["id"], result["state"], result["reason"] or "-")
        if notify and self.notifier and result["state"] in ("done", "paused", "failed"):
            self.notifier.job_ended(self.get(j["id"]), self.projects.get(j["project_id"]))

    def _event(self, job_id: str, ev: dict) -> None:
        j = self.db.one("SELECT project_id FROM jobs WHERE id=?", (job_id,))
        if not j:
            return
        d = job_dir(self.projects.paths(j["project_id"]), job_id)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now(), **ev}, ensure_ascii=False) + "\n")


def setup_project(paths, skill_dir) -> None:
    """The project folder as Claude Code needs it: the skill under .claude/skills (a link into the image)."""
    link = paths.data / ".claude" / "skills" / "video-editing"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() and str(link.readlink()) == str(skill_dir):
        return
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(skill_dir, target_is_directory=True)
    (paths.work / "tmp").mkdir(parents=True, exist_ok=True)


def next_cut(name: str) -> str:
    import re
    m = re.fullmatch(r"(.*?)(\d+)", name)
    return f"{m.group(1)}{int(m.group(2)) + 1}" if m else f"{name}2"
