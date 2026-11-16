"""How a job's command is run.

LocalExecutor runs it as a child process of the web app: for development and tests (with a simulated claude), and
for operators who do not want the isolation of the runner. RunnerClient hands it to the runner (runner.py), which
starts an isolated container per job; the web app itself has no Docker socket and no Claude credentials.

A spec is {"job", "project", "argv", "cwd", "env", "stdout", "stderr"}: stdout and stderr are files in the work
tree that the scheduler reads.
"""
from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import threading

from . import config as config_mod


class LocalExecutor:
    def __init__(self, cfg, env=None):
        self.cfg = cfg
        self.base_env = os.environ if env is None else env
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def start(self, spec) -> None:
        auth = config_mod.claude_auth(self.base_env)           # exactly one credential, or a clear error
        env = {k: v for k, v in self.base_env.items()
               if not k.startswith(("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"))}
        env.update(spec.get("env") or {})
        env.update(auth.env())
        env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1")
        with open(spec["stdout"], "ab") as out, open(spec["stderr"], "ab") as err:
            proc = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=env, stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        with self.lock:
            self.procs[spec["job"]] = proc

    def poll(self, job) -> int | None:
        with self.lock:
            proc = self.procs.get(job)
        if proc is None:
            return -1
        code = proc.poll()
        if code is not None:
            with self.lock:
                self.procs.pop(job, None)
        return code

    def alive(self, job) -> bool:
        with self.lock:
            proc = self.procs.get(job)
        return proc is not None and proc.poll() is None

    def stop(self, job, timeout=10) -> None:
        with self.lock:
            proc = self.procs.pop(job, None)
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=30):
        super().__init__("runner", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class RunnerError(RuntimeError):
    pass


class RunnerClient:
    """Talks to the runner over its Unix socket (a file in the work tree)."""

    def __init__(self, socket_path):
        self.path = str(socket_path)

    def _call(self, method, url, body=None):
        conn = _UnixConnection(self.path)
        try:
            conn.request(method, url, body=json.dumps(body) if body is not None else None,
                         headers={"content-type": "application/json"})
            r = conn.getresponse()
            data = json.loads(r.read() or b"{}")
        except (OSError, ValueError) as e:
            raise RunnerError(f"the runner does not answer ({e}); is the runner service running?")
        finally:
            conn.close()
        if r.status >= 400:
            raise RunnerError(data.get("error") or f"runner: HTTP {r.status}")
        return data

    def status(self) -> dict:
        return self._call("GET", "/status")

    def start(self, spec) -> None:
        self._call("POST", "/jobs", spec)

    def poll(self, job) -> int | None:
        r = self._call("GET", f"/jobs/{job}")
        return None if r.get("running") else int(r.get("exit_code", -1))

    def alive(self, job) -> bool:
        try:
            return bool(self._call("GET", f"/jobs/{job}").get("running"))
        except RunnerError:
            return False

    def stop(self, job, timeout=10) -> None:
        try:
            self._call("DELETE", f"/jobs/{job}")
        except RunnerError:
            pass
