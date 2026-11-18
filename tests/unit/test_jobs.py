import dataclasses
import datetime
import http.server
import json
import socket
import threading
import time

import pytest

from conftest import FIXTURES
from cutlery import cutter_project, notify, stream
from cutlery.db import Database
from cutlery.executor import LocalExecutor
from cutlery.jobs import JobError, Jobs, job_dir, next_cut
from cutlery.projects import Projects
from test_sidecar import GOOD

FAKE = str(FIXTURES / "fake_claude.py")


# ---------------------------------------------------------------- stream-json

class TestStream:
    def test_events(self):
        assert stream.parse_line('{"type":"system","subtype":"init","model":"claude-opus-5-5","session_id":"s1"}') == [
            {"kind": "start", "text": "model claude-opus-5-5", "model": "claude-opus-5-5", "session_id": "s1"}]
        ev = stream.parse_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Reading the transcripts."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cutter analyze --json"}}]}}))
        assert [(e["kind"], e["text"]) for e in ev] == [("text", "Reading the transcripts."), ("tool", "cutter analyze --json")]
        assert stream.parse_line('{"type":"user","message":{}}') == []
        assert stream.parse_line("not json")[0]["kind"] == "error"

    def test_success(self):
        r = {"kind": "result", "subtype": "success", "is_error": False, "text": "cut1 is ready"}
        assert stream.outcome(r, 0)["state"] == "done"

    def test_usage_limit_pauses_with_the_reset_time(self):
        r = {"subtype": "error_during_execution", "is_error": True, "text": "You've hit your session limit · resets 3:45pm"}
        o = stream.outcome(r, 1)
        assert (o["state"], o["reason"]) == ("paused", "usage_limit")
        reset = datetime.datetime.fromisoformat(o["resume_after"]).astimezone()
        assert (reset.hour, reset.minute) == (15, 45)

    def test_reset_from_an_epoch(self):
        assert stream.reset_time("Claude AI usage limit reached|1700000000") == "2023-11-14T22:13+00:00"

    @pytest.mark.parametrize("text", ["OAuth token has expired · Please run /login", "Invalid API key",
                                      "API Error: 401 Invalid authentication credentials", "OAuth token revoked"])
    def test_auth_errors_pause(self, text):
        assert stream.outcome({"is_error": True, "subtype": "error_during_execution", "text": text}, 1)["reason"] == "auth"

    def test_auth_error_only_on_stderr(self):
        assert stream.outcome(None, 1, "Error: Invalid API key")["reason"] == "auth"

    def test_max_turns_and_crash_fail(self):
        assert stream.outcome({"is_error": True, "subtype": "error_max_turns", "text": ""}, 1)["reason"] == "max_turns"
        assert stream.outcome(None, 139, "segmentation fault")["reason"] == "crash"


def test_next_cut():
    assert (next_cut("cut1"), next_cut("cut9"), next_cut("F7")) == ("cut2", "cut10", "F8")


# ---------------------------------------------------------------- jobs with the simulated claude

class Env:
    def __init__(self, cfg, notifier=None, **over):
        self.cfg = dataclasses.replace(cfg, claude_bin=FAKE, skill_dir="/opt/cutter/skills/video-editing", **over)
        self.db = Database(cfg.database)
        self.projects = Projects(self.cfg, self.db)
        self.executor = LocalExecutor(self.cfg)
        self.jobs = Jobs(self.cfg, self.db, self.projects, self.executor, notifier)
        self.jobs.start()

    def project(self, name="Film", analysed=True, briefing=True, footage=True):
        pid = self.projects.create(name)["id"]
        paths = self.projects.paths(pid)
        if footage:
            cid = self.projects.register(pid, GOOD)["id"]
            self.projects.mark(pid, cid, "uploaded")
        if analysed:
            a = cutter_project.analysis_dir(paths)
            a.mkdir(parents=True, exist_ok=True)
            (a / "clips.json").write_text(json.dumps([{"id": "C001", "original": {"fingerprint": GOOD["fingerprint"]}}]))
            (a / "image").mkdir(exist_ok=True)                    # the last stage of cutter analyze: done
            (a / "image" / "C001.json").write_text("{}")
        if briefing:
            (paths.data / "briefing.md").write_text("# Briefing\n")
        return pid

    def wait(self, job_id, *states, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            j = self.jobs.get(job_id)
            if j["state"] in states:
                return j
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} is {self.jobs.get(job_id)['state']}, not {states}")


@pytest.fixture
def env(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_SCENARIO", raising=False)
    e = Env(cfg)
    yield e
    e.jobs.close()


def test_draft_runs_to_done(env, tmp_path, monkeypatch):
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))
    pid = env.project()
    j = env.jobs.create(pid, "draft")
    assert (j["state"], j["cut"], j["model"]) == ("queued", "cut1", "claude-opus-5-5")
    j = env.wait(j["id"], "done")
    assert (j["turns"], j["cost_usd"]) == (42, 3.21) and j["session_id"]
    assert "cut1 is ready" in j["message"]
    paths = env.projects.paths(pid)
    assert (paths.data / "cuts/cut1/edit.json").is_file()
    assert cutter_project.cuts(paths) == ["cut1"]
    kinds = [e["kind"] for e in env.jobs.events(j["id"])]
    assert kinds[:2] == ["queued", "start"] and "tool" in kinds and kinds[-2:] == ["result", "done"]
    call = json.loads(calls.read_text().splitlines()[0])
    argv = call["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "Bash" in argv and "--dangerously-skip-permissions" not in argv
    assert str(paths.work) in argv[argv.index("-p") + 1]             # the prompt says where the analysis is
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("-p") + 1].startswith("/video-editing ")
    assert call["auth"] == ["ANTHROPIC_API_KEY"] and call["nonessential"] == "1"
    assert call["cwd"] == str(paths.data) and call["config_dir"].startswith(str(paths.work / "jobs"))
    assert (paths.data / ".claude/skills/video-editing").is_symlink()


def test_one_active_job_per_project_and_preconditions(env):
    with pytest.raises(JobError) as e:
        env.jobs.create(env.project(analysed=False, footage=False), "draft")
    assert e.value.key == "jobs.error.no_footage"
    with pytest.raises(JobError) as e:
        env.jobs.create(env.project(briefing=False), "draft")
    assert e.value.key == "jobs.error.no_briefing"
    with pytest.raises(JobError) as e:
        env.jobs.create(env.project(), "next")
    assert e.value.key == "jobs.error.no_cut"
    pid = env.project()
    env.jobs.create(pid, "draft")
    with pytest.raises(JobError) as e:
        env.jobs.create(pid, "draft")
    assert e.value.key == "jobs.error.active"


def test_a_draft_waits_until_the_analysis_is_done(env):
    pid = env.project(analysed=False)
    j = env.jobs.create(pid, "draft")                     # allowed before the analysis is ready
    time.sleep(1.5)
    assert env.jobs.get(j["id"])["state"] == "queued"
    assert [e["reason"] for e in env.jobs.events(j["id"]) if e["kind"] == "waiting"] == ["analysis"]
    a = cutter_project.analysis_dir(env.projects.paths(pid))
    a.mkdir(parents=True, exist_ok=True)
    (a / "clips.json").write_text('[{"id": "C001"}]')
    assert env.wait(j["id"], "done")["state"] == "done"


def test_next_cut_after_the_draft(env):
    pid = env.project()
    env.wait(env.jobs.create(pid, "draft")["id"], "done")
    with pytest.raises(JobError):
        env.jobs.create(pid, "draft")                     # there is a cut already
    j = env.wait(env.jobs.create(pid, "next")["id"], "done")
    assert j["cut"] == "cut2" and cutter_project.cuts(env.projects.paths(pid)) == ["cut1", "cut2"]


def test_usage_limit_pauses_and_resume_starts_a_fresh_session(env, monkeypatch, tmp_path):
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "usage_limit")
    pid = env.project()
    j = env.wait(env.jobs.create(pid, "draft")["id"], "paused")
    assert j["reason"] == "usage_limit" and j["resume_after"]
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "ok")
    j = env.jobs.resume(j["id"])
    assert (j["state"], j["attempt"]) == ("queued", 2)
    j = env.wait(j["id"], "done")
    prompts = [json.loads(x)["argv"] for x in calls.read_text().splitlines()]
    second = prompts[1][prompts[1].index("-p") + 1]
    assert "earlier session" in second and "attempt 2" in second
    d = job_dir(env.projects.paths(pid), j["id"])
    assert (d / "attempt-1.jsonl").is_file() and (d / "attempt-2.jsonl").is_file()
    configs = [json.loads(x)["config_dir"] for x in calls.read_text().splitlines()]
    assert configs[0] != configs[1]                         # a fresh Claude Code configuration per session


def test_auth_error_pauses(env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "auth")
    j = env.wait(env.jobs.create(env.project(), "draft")["id"], "paused")
    assert j["reason"] == "auth"


def test_crash_and_max_turns_fail(env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "crash")
    j = env.wait(env.jobs.create(env.project(), "draft")["id"], "failed")
    assert j["reason"] == "crash" and "segmentation fault" in j["message"]
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "max_turns")
    j = env.wait(env.jobs.create(env.project(), "draft")["id"], "failed")
    assert j["reason"] == "max_turns"


def test_without_credentials_the_job_waits_paused_and_resumes_once_one_is_set(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    j = env.wait(env.jobs.create(env.project(), "draft")["id"], "paused")
    assert j["reason"] == "no_credential" and "No Claude authentication" in j["message"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env.jobs.resume(j["id"])
    assert env.wait(j["id"], "done", "failed", "paused")["state"] == "done"


def test_parallel_limit_and_cancel(env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")
    monkeypatch.setenv("FAKE_CLAUDE_SECONDS", "30")
    a = env.jobs.create(env.project("A"), "draft")
    b = env.jobs.create(env.project("B"), "draft")
    env.wait(a["id"], "running")
    time.sleep(1.5)
    assert env.jobs.get(b["id"])["state"] == "queued"           # parallel = 1
    t0 = time.time()
    assert env.jobs.cancel(a["id"])["state"] == "cancelled"
    assert time.time() - t0 < 12
    env.wait(b["id"], "running")
    env.jobs.cancel(b["id"])


def test_restart_while_running_pauses_the_job(cfg, env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")
    j = env.wait(env.jobs.create(env.project(), "draft")["id"], "running")
    env.jobs.close()
    env.executor.stop(j["id"])                                   # the old process is gone with the old web app
    again = Env(cfg)
    try:
        j = again.wait(j["id"], "paused")
        assert j["reason"] == "interrupted"
    finally:
        again.jobs.close()


def test_timeout(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")
    e = Env(cfg, job_timeout_s=1)
    try:
        j = e.wait(e.jobs.create(e.project(), "draft")["id"], "failed")
        assert j["reason"] == "timeout"
    finally:
        e.jobs.close()


# ---------------------------------------------------------------- notifications

def test_webhook_on_pause(cfg, monkeypatch):
    got = []

    class Hook(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "usage_limit")
    c = dataclasses.replace(cfg, webhook_url=f"http://127.0.0.1:{srv.server_port}/hook",
                            public_url="https://cutlery.example")
    e = Env(c, notifier=notify.Notifier(c))
    try:
        j = e.wait(e.jobs.create(e.project("Island"), "draft")["id"], "paused")
        end = time.time() + 5
        while not got and time.time() < end:
            time.sleep(0.05)
        assert got[0]["event"] == "job_paused" and got[0]["job"]["reason"] == "usage_limit"
        assert got[0]["project"]["name"] == "Island" and got[0]["url"].startswith("https://cutlery.example/projects/")
    finally:
        e.jobs.close()
        srv.shutdown()


def test_mqtt_publish_without_auth():
    """A minimal broker: accepts the connection, records the publish."""
    received = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def broker():
        conn, _ = srv.accept()
        data = conn.recv(1024)
        received.append(data)
        conn.sendall(b"\x20\x02\x00\x00")
        buf = b""
        conn.settimeout(2)
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        received.append(buf)
        conn.close()

    t = threading.Thread(target=broker, daemon=True)
    t.start()
    notify.mqtt_publish("127.0.0.1", srv.getsockname()[1], "cutlery/jobs", b'{"event":"job_done"}')
    t.join(5)
    connect, rest = received
    assert connect[0] == 0x10 and b"MQTT" in connect and connect[9] == 0x02        # clean session, no user
    assert rest[0] == 0x30 and b"cutlery/jobs" in rest and rest.endswith(b"\xe0\x00")


def test_mqtt_packets_with_user_and_long_payload():
    connect, publish, _ = notify.mqtt_packets("cutlery", "t", b"x" * 300, "u", "p")
    assert connect[9] & 0xC0 == 0xC0
    assert publish[1:3] == bytes([0xAF, 0x02])             # remaining length 2 + 1 + 300 = 303 in two bytes


# ---------------------------------------------------------------- API

def test_api_briefing_jobs_and_events(client, app, cfg, monkeypatch):
    from test_app import TestProjects

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app.state.jobs.cfg = dataclasses.replace(app.state.jobs.cfg, claude_bin=FAKE)
    pid = TestProjects().create(client, "Island 2026")
    r = client.post(f"/api/projects/{pid}/jobs", json={"kind": "draft"}, headers={"Accept-Language": "de"})
    assert r.status_code == 409 and r.json()["message"] == "Bitte füge zuerst das Footage hinzu."
    cid = client.post(f"/api/projects/{pid}/clips", json=GOOD).json()["id"]
    app.state.projects.mark(pid, cid, "uploaded")
    paths = app.state.projects.paths(pid)
    a = cutter_project.analysis_dir(paths)
    a.mkdir(parents=True)
    (a / "clips.json").write_text(json.dumps([{"id": "C001", "original": {"fingerprint": GOOD["fingerprint"]}}]))
    (a / "image").mkdir(exist_ok=True)                    # the last stage of cutter analyze: done
    (a / "image" / "C001.json").write_text("{}")
    assert client.put(f"/api/projects/{pid}/briefing", json={"prompt": ""}).status_code == 422
    r = client.put(f"/api/projects/{pid}/briefing", headers={"Accept-Language": "de"},
                   json={"prompt": "Zwei Wochen Ringstraße.", "length": "ca. 10 Minuten", "music": "own_and_search"})
    assert r.status_code == 200
    md = (paths.data / "briefing.md").read_text()
    assert "## Was soll der Film erzählen?" in md and "Meine Musik, Rest suchen" in md
    assert "Nicht angegeben (selbst entscheiden)" in md and "Stil" in md
    assert client.get(f"/api/projects/{pid}/briefing").json()["length"] == "ca. 10 Minuten"
    j = client.post(f"/api/projects/{pid}/jobs", json={"kind": "draft"}).json()
    body = client.get(f"/api/jobs/{j['id']}/events").text           # streams until the job has ended
    assert "event: log" in body and '"kind": "result"' in body and '"state": "done"' in body
    listing = client.get(f"/api/projects/{pid}/jobs").json()
    assert listing["jobs"][0]["state"] == "done" and listing["cuts"] == ["cut1"]
    raw = client.get(f"/api/jobs/{j['id']}/raw")
    assert raw.status_code == 200 and '"type": "result"' in raw.text
    assert client.post(f"/api/jobs/{j['id']}/resume").status_code == 409


def test_claude_status_warns_before_the_token_expires(client, app, monkeypatch, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text("sk-ant-oat-x")
    tok.chmod(0o600)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN_FILE", str(tok))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.get("/api/claude").json()
    assert r["kind"] == "oauth" and r["warn"] is False                     # no expiry configured


def test_token_expiry_warning(cfg, monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from conftest import PASSWORD, auth_header
    from cutlery.app import create_app
    from cutter.review_app import set_password

    tok = tmp_path / "tok"
    tok.write_text("sk-ant-oat-x")
    tok.chmod(0o600)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN_FILE", str(tok))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    soon = (datetime.date.today() + datetime.timedelta(days=12)).isoformat()
    set_password.store(str(cfg.htpasswd), "cutlery", PASSWORD)
    with TestClient(create_app(dataclasses.replace(cfg, token_expires=soon)), headers=auth_header()) as c:
        r = c.get("/api/claude").json()
    assert (r["kind"], r["days_left"], r["warn"]) == ("oauth", 12, True)


def test_a_session_that_ends_without_a_cut_has_failed(env, monkeypatch):
    """Claude may end normally and say why it could not cut; that is not "done"."""
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "no_cut")
    pid = env.project()
    j = env.wait(env.jobs.create(pid, "draft")["id"], "failed", "done")
    assert j["state"] == "failed" and j["reason"] == "no_cut" and "analysis files" in j["message"]
