import json
import os
import socket
import threading

import pytest

from cutlery import runner as runner_mod
from cutlery.runner import EgressProxy, Runner, RunnerConfigError, allowed

JOB, PROJECT = "0123456789abcdef", "abcdef1234"
DATA, WORK = f"/data/projects/{PROJECT}", f"/work/projects/{PROJECT}"


class FakeDocker:
    def __init__(self, mounts=None):
        self.calls = []
        self.mounts = mounts if mounts is not None else {
            "/data": "/srv/cutlery/data", "/work": "/srv/cutlery/work", "/models": "/srv/cutlery/models",
            "/run/secrets/anthropic-api-key": "/srv/secrets/key"}

    def self_container(self):
        return {"Id": "self123", "Image": "sha256:cafe", "Mounts": [{"Destination": d, "Source": s} for d, s in self.mounts.items()]}

    def call(self, method, url, body=None, timeout=60):
        self.calls.append((method, url, body))
        if url.startswith("/containers/create"):
            return {"Id": "c0ffee"}
        return {}


@pytest.fixture
def runner(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: os.stat_result((0o40755, 0, 0, 0, 1000, 1000, 0, 0, 0, 0)))
    env = {"ANTHROPIC_API_KEY_FILE": "/run/secrets/anthropic-api-key"}
    key = tmp_path / "key"
    key.write_text("sk-test")
    key.chmod(0o600)
    monkeypatch.setattr(runner_mod.config_mod, "_secret", lambda e, name: "sk-test" if name == "ANTHROPIC_API_KEY" else "")
    return Runner(cfg, FakeDocker(), env=env)


def spec(**over):
    s = {"job": JOB, "project": PROJECT, "argv": ["claude", "-p", "/video-editing cut", "--model", "m"], "cwd": DATA,
         "stdout": f"{WORK}/jobs/{JOB}/attempt-1.jsonl", "stderr": f"{WORK}/jobs/{JOB}/attempt-1.err",
         "env": {"CUTTER_PROJECT": DATA, "TMPDIR": f"{WORK}/tmp", "CLAUDE_CONFIG_DIR": f"{WORK}/jobs/{JOB}/claude-1",
                 "HOME": f"{WORK}/jobs/{JOB}/claude-1"}}
    s.update(over)
    return s


def test_the_container_sees_only_its_project_and_no_network(runner):
    c = runner.container_spec(runner.check_spec(spec()))
    assert c["Image"] == "sha256:cafe" and c["User"] == "1000:1000" and c["WorkingDir"] == DATA
    assert c["HostConfig"]["Binds"] == [f"/srv/cutlery/data/projects/{PROJECT}:{DATA}:rw",
                                        f"/srv/cutlery/work/projects/{PROJECT}:{WORK}:rw",
                                        "/srv/cutlery/models:/models:ro",
                                        "/srv/secrets/key:/run/secrets/claude:ro"]
    hc = c["HostConfig"]
    assert hc["NetworkMode"] == runner_mod.NETWORK and hc["CapDrop"] == ["ALL"]
    assert hc["ReadonlyRootfs"] is True and "no-new-privileges" in hc["SecurityOpt"]
    env = dict(e.split("=", 1) for e in c["Env"])
    assert env["HTTPS_PROXY"] == "http://cutlery-egress:3128" and env["CUTLERY_SECRET_VAR"] == "ANTHROPIC_API_KEY"
    assert "sk-test" not in str(c)                                     # the secret is a file, never in the spec
    assert c["Entrypoint"] == ["cutlery-job"] and c["Cmd"][0] == "claude"


@pytest.mark.parametrize("bad", [
    {"project": "../etc"}, {"job": "x;rm"}, {"cwd": "/data"}, {"cwd": "/data/projects/other"},
    {"stdout": "/work/projects/other/x"}, {"stdout": f"{WORK}/jobs/{JOB}/../../../../etc/passwd"},
    {"env": {"LD_PRELOAD": "/x"}}, {"env": {"HOME": "/root"}}, {"env": {"TMPDIR": "/work/projects/other/tmp"}},
    {"argv": ["sh", "-c", "id"]}, {"argv": ["claude", "--dangerously-skip-permissions"]}, {"argv": ["claude", "--bare"]},
    {"argv": "claude"},
])
def test_requests_from_the_web_app_are_checked(runner, bad):
    with pytest.raises(ValueError):
        runner.check_spec(spec(**bad))


def test_start_state_and_stop(runner):
    runner.start(spec())
    methods = [(m, u.split("?")[0]) for m, u, _ in runner.docker.calls]
    assert ("POST", "/containers/create") in methods and ("POST", "/containers/c0ffee/start") in methods
    runner.docker.call = lambda m, u, b=None, timeout=60: {"State": {"Running": False, "ExitCode": 3}} if m == "GET" else {}
    assert runner.state(JOB) == {"running": False, "exit_code": 3}
    assert runner.state(JOB) == {"running": False, "exit_code": 3}      # remembered after the container is gone
    with pytest.raises(ValueError):
        runner.state("../x")
    # resuming starts the same job again: the old exit code must not be reported for the new attempt
    runner.docker.call = lambda m, u, b=None, timeout=60: (
        {"Id": "c0ffee"} if u.startswith("/containers/create") else
        {"State": {"Running": True}} if m == "GET" else {})
    runner.start(spec())
    assert runner.state(JOB) == {"running": True}


def test_refuses_to_start_without_mounts_or_with_two_credentials(cfg, monkeypatch):
    monkeypatch.setattr(runner_mod.config_mod, "_secret", lambda e, name: "x" if name == "ANTHROPIC_API_KEY" else "")
    with pytest.raises(RunnerConfigError, match="/models"):
        Runner(cfg, FakeDocker({"/data": "/a", "/work": "/b"}), env={"ANTHROPIC_API_KEY_FILE": "/run/secrets/k"})
    with pytest.raises(RunnerConfigError, match="file"):
        Runner(cfg, FakeDocker(), env={"ANTHROPIC_API_KEY": "x"})
    monkeypatch.setattr(runner_mod.config_mod, "_secret", lambda e, name: "x")
    with pytest.raises(runner_mod.config_mod.ConfigError, match="Both"):
        Runner(cfg, FakeDocker(), env={})


def test_allowlist():
    allow = runner_mod.DEFAULT_ALLOW
    assert allowed("api.anthropic.com", allow) and allowed("ia800300.us.archive.org", allow)
    assert allowed("API.Anthropic.com.", allow)
    assert not allowed("evil.com", allow) and not allowed("anthropic.com.evil.com", allow)
    assert not allowed("notarchive.org", allow) and not allowed("statsig.anthropic.com", allow)


def proxy_request(port, line):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(line.encode() + b"\r\nHost: x\r\n\r\n")
        return s.recv(200).decode()


def test_egress_proxy_refuses_what_is_not_allowed():
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    proxy = EgressProxy(("127.0.0.1", 0), ["localhost"])
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    port = proxy.server_address[1]
    try:
        assert "403" in proxy_request(port, "CONNECT evil.example:443 HTTP/1.1")
        assert "403" in proxy_request(port, "CONNECT localhost:22 HTTP/1.1")            # HTTPS only
        assert "405" in proxy_request(port, "GET http://evil.example/ HTTP/1.1")       # no plain HTTP
    finally:
        proxy.shutdown()
        upstream.close()


def test_without_a_credential_the_runner_starts_and_claude_jobs_wait(cfg, monkeypatch):
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: os.stat_result((0o40755, 0, 0, 0, 1000, 1000, 0, 0, 0, 0)))
    monkeypatch.setattr(runner_mod.config_mod, "_secret", lambda e, name: "")
    r = Runner(cfg, FakeDocker({"/data": "/a", "/work": "/b", "/models": "/c"}), env={})
    assert r.status()["kind"] is None and "No Claude authentication" in r.status()["auth_error"]
    with pytest.raises(runner_mod.NoCredentialError):
        r.start(spec())
    assert not r.docker.calls                                          # nothing was started
    r.start(spec(argv=["cutter", "analyze", "--json"], kind="analysis"))  # the analysis needs no credential
    assert any(u.startswith("/containers/create") for _, u, _ in r.docker.calls)


TOKEN = "sk-ant-oat01-" + "A1b2_C3d4-" * 8


@pytest.fixture
def bare_runner(cfg, monkeypatch, tmp_path):
    """A runner without a credential in .env, with CUTLERY_SECRETS_DIR mounted (host path /srv/cutlery/secrets)."""
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: os.stat_result((0o40755, 0, 0, 0, 1000, 1000, 0, 0, 0, 0))
                        if str(p) == "/data" else real(p, *a, **k))
    monkeypatch.setattr(runner_mod.config_mod, "_secret", lambda e, name: "")
    monkeypatch.setattr(os, "chown", lambda *a: None)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    monkeypatch.setattr(runner_mod, "SECRETS", str(secrets))
    mounts = {"/data": "/a", "/work": "/b", "/models": "/c", str(secrets): "/srv/cutlery/secrets"}
    return Runner(cfg, FakeDocker(mounts), env={}), secrets


def test_a_token_entered_in_the_web_ui_is_stored_for_the_jobs_only(bare_runner):
    r, secrets = bare_runner
    assert r.status()["can_store"] and r.status()["source"] is None
    r.set_token(TOKEN)
    f = secrets / runner_mod.STORED_TOKEN
    assert f.read_text().strip() == TOKEN and (f.stat().st_mode & 0o777) == 0o600
    st = r.status()
    assert (st["kind"], st["source"]) == ("oauth", "stored") and st["stored"] and st["expires"] > st["stored"]
    assert TOKEN not in json.dumps(st)                                 # never sent back
    c = r.container_spec(r.check_spec(spec()))
    assert f"/srv/cutlery/secrets/{runner_mod.STORED_TOKEN}:/run/secrets/claude:ro" in c["HostConfig"]["Binds"]
    assert dict(e.split("=", 1) for e in c["Env"])["CUTLERY_SECRET_VAR"] == "CLAUDE_CODE_OAUTH_TOKEN"
    # a restarted runner finds it again
    again = Runner(r.cfg, FakeDocker(r.docker.mounts), env={})
    assert again.status()["source"] == "stored"
    r.clear_token()
    assert not f.exists() and r.status()["kind"] is None
    with pytest.raises(runner_mod.NoCredentialError):
        r.start(spec())


@pytest.mark.parametrize("bad", ["", "hello", "sk-ant-short", "sk-ant-oat01-" + "x" * 30 + "\nrm -rf /", TOKEN + " "  + TOKEN])
def test_only_claude_tokens_are_stored(bare_runner, bad):
    r, secrets = bare_runner
    with pytest.raises(ValueError):
        r.set_token(bad)
    assert not any(secrets.iterdir())


def test_a_credential_in_env_wins_and_cannot_be_changed_from_the_web_ui(runner):
    assert runner.status()["source"] == "env" and not runner.status()["can_store"]
    with pytest.raises(ValueError, match=".env"):
        runner.set_token(TOKEN)


def test_an_analysis_stops_older_analyses_of_the_project(runner):
    """After an update the web app starts the analysis again; the run from before must not go on beside it."""
    def containers(method, url, body=None, timeout=60):
        runner.docker.calls.append((method, url, body))
        if url.startswith("/containers/json"):
            return [
                {"Id": "a", "Labels": {"cutlery.job": "aaaa1111", "cutlery.project": PROJECT}, "Command": "cutlery-job cutter analyze"},
                {"Id": "b", "Labels": {"cutlery.job": "bbbb2222", "cutlery.project": PROJECT, "cutlery.kind": "analysis"}},
                {"Id": "c", "Labels": {"cutlery.job": "cccc3333", "cutlery.project": PROJECT, "cutlery.kind": "claude"}},
                {"Id": "d", "Labels": {"cutlery.job": JOB, "cutlery.project": PROJECT, "cutlery.kind": "analysis"}},
            ]
        return {"Id": "c0ffee"} if url.startswith("/containers/create") else {}
    runner.docker.call = containers
    runner.start(spec(kind="analysis", argv=["cutter", "analyze"],
                      env={"CUTTER_PROJECT": DATA, "TMPDIR": f"{WORK}/tmp"}))
    stopped = [u for m, u, _ in runner.docker.calls if m == "POST" and u.endswith("/stop?t=10")]
    assert stopped == ["/containers/cutlery-job-aaaa1111/stop?t=10", "/containers/cutlery-job-bbbb2222/stop?t=10"]
    create = next(b for m, u, b in runner.docker.calls if u.startswith("/containers/create"))
    assert create["Labels"]["cutlery.kind"] == "analysis"
