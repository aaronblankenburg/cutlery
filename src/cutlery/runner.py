"""The runner: the only part of Cutlery with access to the Docker socket and the Claude credentials.

The web app asks it over a Unix socket (WORK_DIR/.runner/runner.sock) to run claude for one project. The runner does
not take mounts, images or users from the request: it builds every job container itself.

    image      its own image (the one the web app runs)
    user       PUID:PGID (the owner of the storage trees)
    mounts     DATA/projects/<id> and WORK/projects/<id> (read-write), MODELS (read-only), the credential file
               (read-only, read into the environment only by the job's entrypoint)
    network    an internal Docker network without a route to the outside; the only way out is the runner's
               egress proxy, which lets through HTTPS to the allowlisted hosts (CUTLERY_EGRESS_ALLOW)
    limits     all capabilities dropped, no-new-privileges, read-only root file system, PIDs and memory limit

Everything the request may set is checked against the project: the working folder, the output files, the
environment (a few keys, values inside the project) and the command (claude with its arguments).
"""
from __future__ import annotations

import http.client
import http.server
import json
import logging
import os
import pathlib
import re
import select
import socket
import socketserver
import threading
import urllib.parse

from . import config as config_mod
from . import egress

log = logging.getLogger("cutlery.runner")

NETWORK = "cutlery-jobs"
PROXY_PORT = 3128
ALIAS = "cutlery-egress"
DEFAULT_ALLOW = ("api.anthropic.com",
                 # music search (see docs: what the agent may reach)
                 "freemusicarchive.org", "*.freemusicarchive.org", "ccmixter.org", "*.ccmixter.org",
                 "commons.wikimedia.org", "upload.wikimedia.org", "archive.org", "*.archive.org",
                 "jamendo.com", "*.jamendo.com",
                 # map outlines (Natural Earth via world-atlas)
                 "cdn.jsdelivr.net", "unpkg.com", "naturalearthdata.com", "*.naturalearthdata.com",
                 # map tiles for route maps (OpenTopoMap: CC-BY-SA, light use allowed)
                 "opentopomap.org", "*.opentopomap.org",
                 # models for the analysis (Whisper, detection), downloaded once into the models folder
                 "huggingface.co", "*.huggingface.co", "*.hf.co")
ENV_KEYS = ("CUTTER_PROJECT", "TMPDIR", "CLAUDE_CONFIG_DIR", "HOME")
SECRET_IN_JOB = "/run/secrets/claude"
SECRETS = "/secrets"                          # CUTLERY_SECRETS_DIR: only the runner mounts it
STORED_TOKEN = "claude-oauth-token"           # a subscription token entered in the web UI
TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,400}")
TOKEN_DAYS = 365                              # `claude setup-token` makes tokens valid for a year
JOB_ID = re.compile(r"^[0-9a-f]{8,32}$")
PROJECT_ID = re.compile(r"^[0-9a-f]{6,32}$")


class RunnerConfigError(Exception):
    pass


# ---------------------------------------------------------------- Docker Engine API over the socket

class Docker:
    def __init__(self, path="/var/run/docker.sock"):
        self.path = path

    def call(self, method, url, body=None, timeout=60):
        conn = http.client.HTTPConnection("docker", timeout=timeout)
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.sock.settimeout(timeout)
        conn.sock.connect(self.path)
        try:
            conn.request(method, url, body=json.dumps(body) if body is not None else None,
                         headers={"content-type": "application/json", "host": "docker"})
            r = conn.getresponse()
            raw = r.read()
        finally:
            conn.close()
        data = json.loads(raw) if raw and raw[:1] in (b"{", b"[") else {}
        if r.status >= 400:
            raise RuntimeError(f"Docker {method} {url}: {r.status} {data.get('message', raw[:200])}")
        return data

    def self_container(self):
        cid = os.environ.get("HOSTNAME", "")
        return self.call("GET", f"/containers/{cid}/json")


# ---------------------------------------------------------------- egress proxy

def allowed(host: str, allow) -> bool:
    host = host.lower().rstrip(".")
    for rule in allow:
        rule = rule.lower()
        if rule.startswith("*."):
            if host.endswith(rule[1:]):
                return True
        elif host == rule:
            return True
    return False


class EgressProxy(socketserver.ThreadingTCPServer):
    """HTTPS-only forward proxy (CONNECT) for an allowlist of hosts. A job container may also reach the hosts the
    user named for its project (egress.py): `extra` maps the container's address on the job network to them."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, allow, extra=None, on_denied=None):
        self.allow = tuple(allow)
        self.extra = extra if extra is not None else {}
        self.on_denied = on_denied or (lambda host, client_ip: None)
        super().__init__(address, _ProxyHandler)

    def allows(self, host: str, client_ip: str) -> bool:
        return allowed(host, self.allow) or allowed(host, self.extra.get(client_ip, ()))


class _ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(4096).decode("latin-1").strip()
        while True:                                     # skip the headers
            h = self.rfile.readline(8192)
            if h in (b"\r\n", b"\n", b""):
                break
        parts = line.split()
        if len(parts) != 3 or parts[0] != "CONNECT":
            self.wfile.write(b"HTTP/1.1 405 Only CONNECT\r\n\r\n")
            return
        host, _, port = parts[1].rpartition(":")
        if port != "443" or not self.server.allows(host, self.client_address[0]):
            log.warning("egress denied: %s", parts[1])
            if port == "443":
                self.server.on_denied(host, self.client_address[0])
            self.wfile.write(b"HTTP/1.1 403 Forbidden by Cutlery egress allowlist\r\n\r\n")
            return
        try:
            upstream = socket.create_connection((host, 443), timeout=20)
        except OSError as e:
            self.wfile.write(f"HTTP/1.1 502 {e.__class__.__name__}\r\n\r\n".encode())
            return
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        self.wfile.flush()
        client = self.connection
        try:
            socks = [client, upstream]
            while True:
                ready, _, err = select.select(socks, [], socks, 300)
                if err or not ready:
                    break
                for s in ready:
                    data = s.recv(65536)
                    if not data:
                        return
                    (upstream if s is client else client).sendall(data)
        except OSError:
            pass
        finally:
            upstream.close()


# ---------------------------------------------------------------- the runner

class NoCredentialError(Exception):
    """No Claude credential is configured; the job waits (paused) until one is."""


class Runner:
    def __init__(self, cfg, docker: Docker | None = None, env=None):
        self.cfg, self.docker = cfg, docker or Docker()
        self.env = os.environ if env is None else env
        # Exactly one credential. Without one the runner still starts (analysis works, the operator may add the
        # token later); Claude Code jobs are refused with the reason, which pauses them until the next attempt.
        # A token entered in the web UI (stored in CUTLERY_SECRETS_DIR) counts when .env has none.
        self.source = None
        try:
            self.auth, self.auth_error, self.source = config_mod.claude_auth(self.env), None, "env"
        except config_mod.MissingAuth as e:
            self.auth, self.auth_error = None, str(e)
        self.allow = tuple(x.strip() for x in (self.env.get("CUTLERY_EGRESS_ALLOW") or ",".join(DEFAULT_ALLOW)).split(",")
                           if x.strip())
        self.memory = int(float(self.env.get("CUTLERY_JOB_MEMORY_GB") or 8) * 1024 ** 3)
        self.exits: dict[str, int] = {}
        self.lock = threading.Lock()
        self.extra: dict[str, tuple] = {}        # container address -> hosts the user named (EgressProxy.extra)
        self.extra_jobs: dict[str, str] = {}     # container address -> job (Claude jobs)
        self.denied: dict[str, list[str]] = {}   # job -> domains it was refused and the user may allow
        me = self.docker.self_container()
        self.image = me["Image"]
        self.mounts = {m["Destination"]: m["Source"] for m in me.get("Mounts", [])}
        for dest in ("/data", "/work", "/models"):
            if dest not in self.mounts:
                raise RunnerConfigError(f"the runner needs {dest} mounted (see compose.yaml)")
        self.secret_source = self.mounts.get(self._secret_path()) if self.auth else None
        if self.auth and not self.secret_source:
            raise RunnerConfigError(f"the credential file {self._secret_path()} must be a bind mount of a host file")
        if self.auth is None:
            self._load_stored()
        if self.auth is None:
            log.warning("cutlery runner: %s Or enter a token in the web UI (Settings). Claude Code jobs pause until "
                        "then.", self.auth_error)
        self.user = self.env.get("CUTLERY_JOB_USER") or f"{os.stat('/data').st_uid}:{os.stat('/data').st_gid}"
        self.container_id = me["Id"]
        self.vision_done = threading.Event()
        self.vision_done.set()                   # serve() clears it while it fetches the models

    # ------------------------------------------------------------ models for the image analysis

    def fetch_vision_models(self, folder="/models/cutter-vision") -> list[str]:
        """Download cutter's detection models (faces, objects, face recognition; 57 MB, checksums verified by
        cutter) into the models tree, for the user the jobs run as. The analysis container cannot fetch them itself:
        they are on GitHub, which the egress allowlist does not open (the agent shares it). Returns the files there."""
        have = []
        try:
            from cutter import image_analysis
        except ImportError:                     # not the Cutlery image (tests)
            self.vision_done.set()
            return have
        os.environ["CUTTER_VISION_MODELS"] = folder
        try:
            uid, gid = (int(x) for x in self.user.split(":"))
        except ValueError:
            uid = gid = -1
        try:
            for name in image_analysis.MODELS:
                try:
                    path = image_analysis.model_file(name)
                except Exception as e:          # network, disk: the analysis runs without them, as before
                    log.warning("vision model %s: %s", name, e)
                    continue
                if path:
                    have.append(path.name)
                    for p in (path, path.parent):
                        try:
                            os.chown(p, uid, gid)
                        except OSError:
                            pass
            log.info("vision models in %s: %s", folder, ", ".join(have) or "none (faces, people and objects are left out)")
        finally:
            self.vision_done.set()
        return have

    # ------------------------------------------------------------ a token entered in the web UI

    def _stored(self) -> pathlib.Path | None:
        return pathlib.Path(SECRETS) / STORED_TOKEN if SECRETS in self.mounts else None

    def _load_stored(self) -> None:
        f = self._stored()
        if f is None or not f.is_file():
            return
        token = f.read_text().strip()
        if not TOKEN_RE.fullmatch(token):
            log.warning("cutlery runner: the stored token in %s is not a Claude token; ignored", f)
            return
        self.auth = config_mod.ClaudeAuth("oauth", config_mod.AUTH_VARIABLES["oauth"], token)
        self.auth_error, self.source = None, "stored"
        self.secret_source = f"{self.mounts[SECRETS].rstrip('/')}/{STORED_TOKEN}"

    def stored_info(self) -> dict:
        f = self._stored()
        try:
            meta = json.loads(f.with_suffix(".json").read_text()) if f else {}
        except (OSError, ValueError):
            meta = {}
        return {"stored": meta.get("stored"), "expires": meta.get("expires")}

    def set_token(self, token: str) -> None:
        token = (token or "").strip()
        if self.source == "env":
            raise ValueError("the Claude access is set in .env; remove it there to enter a token here")
        if self._stored() is None:
            raise ValueError("CUTLERY_SECRETS_DIR is not mounted into the runner (see compose.yaml)")
        if not TOKEN_RE.fullmatch(token):
            raise ValueError("this is not a Claude token (it starts with sk-ant-; create one with claude setup-token)")
        import datetime
        f = self._stored()
        uid, gid = (int(x) for x in self.user.split(":"))
        today = datetime.date.today()
        meta = {"stored": today.isoformat(), "expires": (today + datetime.timedelta(days=TOKEN_DAYS)).isoformat()}
        with self.lock:
            for path, text in ((f, token + "\n"), (f.with_suffix(".json"), json.dumps(meta) + "\n")):
                tmp = path.with_name(path.name + ".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(text)
                try:
                    os.chown(tmp, uid, gid)          # the job reads it as PUID; the web app never mounts this folder
                except PermissionError:
                    pass
                os.replace(tmp, path)
            self.source = None
            self._load_stored()
        log.info("cutlery runner: a Claude token was entered in the web UI")

    def clear_token(self) -> None:
        if self.source != "stored":
            raise ValueError("no token entered in the web UI is in use")
        with self.lock:
            f = self._stored()
            f.unlink(missing_ok=True)
            f.with_suffix(".json").unlink(missing_ok=True)
            self.auth, self.source, self.secret_source = None, None, None
            self.auth_error = "No Claude authentication is configured. " + config_mod.AUTH_HELP
        log.info("cutlery runner: the Claude token of the web UI was removed")

    def _secret_path(self) -> str:
        name = self.auth.variable + "_FILE"
        path = (self.env.get(name) or "").strip()
        if not path:
            raise RunnerConfigError(f"the runner reads the credential from a file: set {name} (not {self.auth.variable})")
        return path

    # ------------------------------------------------------------ network

    def setup_network(self) -> None:
        try:
            self.docker.call("GET", f"/networks/{NETWORK}")
        except RuntimeError:
            self.docker.call("POST", "/networks/create", {"Name": NETWORK, "Internal": True,
                                                          "Labels": {"cutlery": "jobs"}})
        net = self.docker.call("GET", f"/networks/{NETWORK}")
        if not net.get("Internal"):
            raise RunnerConfigError(f"Docker network {NETWORK} exists but is not internal; remove it")
        if self.container_id not in (net.get("Containers") or {}):
            self.docker.call("POST", f"/networks/{NETWORK}/connect",
                             {"Container": self.container_id, "EndpointConfig": {"Aliases": [ALIAS]}})

    def cleanup(self) -> None:
        """Remove finished job containers left from before (running ones keep running)."""
        flt = urllib.parse.quote(json.dumps({"label": ["cutlery.job"], "status": ["exited", "created", "dead"]}))
        for c in self.docker.call("GET", f"/containers/json?all=1&filters={flt}"):
            self.docker.call("DELETE", f"/containers/{c['Id']}?force=1")

    # ------------------------------------------------------------ jobs

    def check_spec(self, spec: dict) -> dict:
        job, project = str(spec.get("job", "")), str(spec.get("project", ""))
        if not JOB_ID.match(job) or not PROJECT_ID.match(project):
            raise ValueError("invalid job or project id")
        data, work = f"/data/projects/{project}", f"/work/projects/{project}"
        jobdir = f"{work}/jobs/{job}/"
        if spec.get("cwd") != data:
            raise ValueError("the working folder must be the project folder")
        for key in ("stdout", "stderr"):
            p = os.path.normpath(str(spec.get(key, "")))
            if not p.startswith(jobdir) or "/../" in p:
                raise ValueError(f"{key} must be inside the job folder")
        env = spec.get("env") or {}
        if set(env) - set(ENV_KEYS):
            raise ValueError(f"environment keys allowed: {', '.join(ENV_KEYS)}")
        for k, v in env.items():
            v = os.path.normpath(str(v))
            if not (v == data or v.startswith(data + "/") or v.startswith(work + "/")):
                raise ValueError(f"{k} must be inside the project")
        kind = spec.get("kind", "claude")
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("the command must be a list of arguments")
        if kind == "claude":
            if argv[0] != "claude" or any(a.startswith("--dangerously") or a == "--bare" for a in argv):
                raise ValueError("the command must be claude with its arguments, without bypassing permissions")
        elif kind == "analysis":
            if argv[:2] != ["cutter", "analyze"]:
                raise ValueError("an analysis job runs cutter analyze")
        else:
            raise ValueError("unknown job kind")
        allow = spec.get("allow") or []
        if (not isinstance(allow, list) or len(allow) > 2 * egress.MAX_DOMAINS
                or not all(egress.valid_rule(r) for r in allow)):
            raise ValueError("allow must be a list of domains")
        return {"job": job, "project": project, "data": data, "work": work, "argv": argv, "env": env, "kind": kind,
                "stdout": spec["stdout"], "stderr": spec["stderr"], "allow": allow if kind == "claude" else []}

    def container_spec(self, s: dict) -> dict:
        host = lambda dest: self.mounts[dest].rstrip("/")          # host path of a tree
        claude = s.get("kind", "claude") == "claude"
        env = {**s["env"], "HTTPS_PROXY": f"http://{ALIAS}:{PROXY_PORT}", "HTTP_PROXY": f"http://{ALIAS}:{PROXY_PORT}",
               "NO_PROXY": "localhost,127.0.0.1", "CUTLERY_JOB_STDOUT": s["stdout"], "CUTLERY_JOB_STDERR": s["stderr"]}
        if claude:
            env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", HF_HUB_OFFLINE="1",
                       NUMBA_CACHE_DIR=f"{s['work']}/tmp/numba", CUTLERY_SECRET_VAR=self.auth.variable)
        binds = [f"{host('/data')}/projects/{s['project']}:{s['data']}:rw",
                 f"{host('/work')}/projects/{s['project']}:{s['work']}:rw"]
        if claude:      # the agent reads the models; only the analysis may add to them (first download)
            binds += [f"{host('/models')}:/models:ro", f"{self.secret_source}:{SECRET_IN_JOB}:ro"]
        else:
            binds += [f"{host('/models')}:/models:rw"]
        return {
            "Image": self.image, "User": self.user, "WorkingDir": s["data"],
            "Entrypoint": ["cutlery-job"], "Cmd": s["argv"],
            "Env": [f"{k}={v}" for k, v in env.items()],
            "Labels": {"cutlery.job": s["job"], "cutlery.project": s["project"], "cutlery.kind": s.get("kind", "claude")},
            "Healthcheck": {"Test": ["NONE"]},
            "HostConfig": {
                "Binds": binds,
                "NetworkMode": NETWORK, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                "ReadonlyRootfs": True, "Tmpfs": {"/tmp": "rw,size=512m"}, "PidsLimit": 1024,
                "Memory": self.memory, "Init": True,
            },
        }

    def stop_other_analyses(self, project: str, keep: str) -> int:
        """One analysis per project: stop analysis containers of the project still running from before (the web app
        restarted, for example on an update, and started a new run; the old one would do the same work again)."""
        flt = urllib.parse.quote(json.dumps({"label": [f"cutlery.project={project}"], "status": ["running"]}))
        n = 0
        for c in self.docker.call("GET", f"/containers/json?filters={flt}"):
            labels = c.get("Labels") or {}
            kind = labels.get("cutlery.kind") or ("analysis" if " cutter analyze" in f" {c.get('Command', '')}" else "claude")
            job = labels.get("cutlery.job")
            if kind != "analysis" or not job or job == keep:
                continue
            log.info("project %s: stopping analysis job %s left from before", project, job)
            self.stop(job)
            n += 1
        return n

    def start(self, spec: dict) -> None:
        s = self.check_spec(spec)
        if s["kind"] == "claude" and self.auth is None:
            raise NoCredentialError(self.auth_error)
        if s["kind"] == "analysis":
            self.vision_done.wait(20)            # the download at start (under the caller's 30 s timeout)
            try:
                self.stop_other_analyses(s["project"], s["job"])
            except RuntimeError as e:
                log.warning("cannot check for older analysis jobs: %s", e)
        with self.lock:
            self.exits.pop(s["job"], None)          # a resumed job has the same id: forget the last attempt's exit
        name = f"cutlery-job-{s['job']}"
        try:
            self.docker.call("DELETE", f"/containers/{name}?force=1")
        except RuntimeError:
            pass
        c = self.docker.call("POST", f"/containers/create?name={name}", self.container_spec(s))
        self.docker.call("POST", f"/containers/{c['Id']}/start")
        self._allow_extra(s["job"], c["Id"], s["allow"])
        log.info("job %s started in container %s", s["job"], c["Id"][:12])

    def _allow_extra(self, job: str, container: str, rules: list[str]) -> None:
        """The hosts the user named for the project, for this container's address only (until the job ends)."""
        try:
            nets = self.docker.call("GET", f"/containers/{container}/json")["NetworkSettings"]["Networks"]
            ip = nets[NETWORK]["IPAddress"]
        except (RuntimeError, KeyError, TypeError) as e:
            log.warning("job %s: no address on %s (%s); only the fixed allowlist applies", job, NETWORK, e)
            ip = None
        with self.lock:
            for k in [k for k, v in self.extra_jobs.items() if v == job or k == ip]:
                self.extra_jobs.pop(k, None)
                self.extra.pop(k, None)
            self.denied.pop(job, None)
            if ip:
                self.extra[ip] = tuple(rules)
                self.extra_jobs[ip] = job
        if rules:
            log.info("job %s may also reach %s", job, ", ".join(r for r in rules if not r.startswith("*.")))

    def note_denied(self, host: str, client_ip: str) -> None:
        """A Claude job was refused a host: keep its domain for the user to allow (EgressProxy.on_denied)."""
        d = egress.registrable(host)
        with self.lock:
            job = self.extra_jobs.get(client_ip)
            if not d or not job:
                return
            lst = self.denied.setdefault(job, [])
            if d not in lst and len(lst) < 20:
                lst.append(d)

    def allow_domain(self, job: str, domain: str) -> None:
        """The user allowed a domain for the running job: it and its subdomains, from now on."""
        if not JOB_ID.match(job) or egress.registrable(domain) != domain:
            raise ValueError("invalid job id or domain")
        with self.lock:
            for ip, j in self.extra_jobs.items():
                if j == job:
                    self.extra[ip] = tuple(dict.fromkeys((*self.extra.get(ip, ()), *egress.rules({domain}))))
            if domain in self.denied.get(job, []):
                self.denied[job].remove(domain)
        log.info("job %s may now also reach %s", job, domain)

    def _forget_extra(self, job: str) -> None:
        with self.lock:
            self.denied.pop(job, None)
            for k in [k for k, v in self.extra_jobs.items() if v == job]:
                self.extra_jobs.pop(k, None)
                self.extra.pop(k, None)

    def state(self, job: str) -> dict:
        if not JOB_ID.match(job):
            raise ValueError("invalid job id")
        with self.lock:
            if job in self.exits:
                return {"running": False, "exit_code": self.exits[job]}
        try:
            c = self.docker.call("GET", f"/containers/cutlery-job-{job}/json")
        except RuntimeError:
            return {"running": False, "exit_code": -1}
        st = c.get("State") or {}
        if st.get("Running"):
            with self.lock:
                return {"running": True, "denied": list(self.denied.get(job, []))}
        code = int(st.get("ExitCode", -1))
        self._forget_extra(job)
        with self.lock:
            self.exits[job] = code
        self.docker.call("DELETE", f"/containers/cutlery-job-{job}?force=1")
        return {"running": False, "exit_code": code}

    def stop(self, job: str) -> None:
        if not JOB_ID.match(job):
            raise ValueError("invalid job id")
        try:
            self.docker.call("POST", f"/containers/cutlery-job-{job}/stop?t=10", timeout=30)
        except RuntimeError:
            pass
        self._forget_extra(job)

    def status(self) -> dict:
        return {"kind": self.auth.kind if self.auth else None, "auth_error": self.auth_error, "source": self.source,
                "can_store": self._stored() is not None and self.source != "env",
                **(self.stored_info() if self.source == "stored" else {}),
                "egress": list(self.allow), "image": self.image[:19]}


# ---------------------------------------------------------------- the API on the Unix socket

class _Api(http.server.BaseHTTPRequestHandler):
    runner: Runner = None

    def _send(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        try:
            if method == "GET" and self.path == "/status":
                return self._send(200, self.runner.status())
            if self.path == "/credential" and method in ("PUT", "DELETE"):
                if method == "DELETE":
                    self.runner.clear_token()
                    return self._send(200, {"ok": True})
                n = int(self.headers.get("content-length") or 0)
                if n > 8 * 1024:
                    return self._send(413, {"error": "request too large"})
                self.runner.set_token(str(json.loads(self.rfile.read(n)).get("token", "")))
                return self._send(200, {"ok": True})
            if method == "POST" and self.path == "/jobs":
                n = int(self.headers.get("content-length") or 0)
                if n > 256 * 1024:
                    return self._send(413, {"error": "request too large"})
                self.runner.start(json.loads(self.rfile.read(n)))
                return self._send(201, {"ok": True})
            m = re.fullmatch(r"/jobs/([0-9a-f]+)", self.path)
            if m and method == "GET":
                return self._send(200, self.runner.state(m.group(1)))
            if m and method == "DELETE":
                self.runner.stop(m.group(1))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/jobs/([0-9a-f]+)/allow", self.path)
            if m and method == "POST":
                n = int(self.headers.get("content-length") or 0)
                if n > 4096:
                    return self._send(413, {"error": "request too large"})
                self.runner.allow_domain(m.group(1), str(json.loads(self.rfile.read(n)).get("domain", "")))
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except NoCredentialError as e:
            return self._send(409, {"error": str(e), "reason": "auth"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            log.exception("runner request failed")
            return self._send(500, {"error": str(e)})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PUT(self):
        self._route("PUT")

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def address_string(self):
        return "web"


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(cfg) -> None:
    try:
        runner = Runner(cfg)
        runner.setup_network()
        runner.cleanup()
    except (config_mod.ConfigError, RunnerConfigError, RuntimeError) as e:
        raise SystemExit(f"cutlery runner: {e}")
    runner.vision_done.clear()
    threading.Thread(target=runner.fetch_vision_models, name="vision-models", daemon=True).start()
    proxy = EgressProxy(("0.0.0.0", PROXY_PORT), runner.allow, runner.extra, runner.note_denied)
    threading.Thread(target=proxy.serve_forever, name="egress", daemon=True).start()
    path = cfg.runner_socket
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    _Api.runner = runner
    server = _UnixServer(str(path), _Api)
    os.chmod(path, 0o660)
    try:
        uid, gid = (int(x) for x in runner.user.split(":"))
        os.chown(path, uid, gid)
        os.chown(path.parent, uid, gid)
    except (ValueError, OSError):
        pass
    log.info("runner ready: %s, egress to %d hosts, Claude %s", path, len(runner.allow),
             runner.auth.kind if runner.auth else "not configured")
    server.serve_forever()
