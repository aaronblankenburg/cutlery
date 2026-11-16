"""The whole stack with Docker Compose: web app and runner, jobs in isolated containers, a simulated claude.

    docker build --build-arg CUTTER_IMAGE=cutter:main -t cutlery:dev .
    docker build -f tests/integration/Dockerfile.fakeclaude -t cutlery:fakeclaude .
    CUTLERY_TEST_STACK_IMAGE=cutlery:fakeclaude pytest -m integration tests/integration/test_stack.py

The simulated claude (tests/fixtures/fake_claude.py) takes its scenario from `.fake-claude-scenario` in the
project folder; the "probe" scenario reports what the job container can do.
"""
import base64
import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.request

import pytest

pytestmark = pytest.mark.integration
IMAGE = os.environ.get("CUTLERY_TEST_STACK_IMAGE")
ROOT = pathlib.Path(__file__).resolve().parents[2]
PORT = 8294


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if not IMAGE or not shutil.which("docker"):
        pytest.skip("set CUTLERY_TEST_STACK_IMAGE (an image with the simulated claude) to run the stack tests")
    d = tmp_path_factory.mktemp("stack")
    for sub in ("data", "work", "models", "secrets"):
        (d / sub).mkdir()
    key = d / "secrets" / "key"
    key.write_text("sk-ant-api-test-only")
    key.chmod(0o600)
    env = d / ".env"
    env.write_text("\n".join([
        f"CUTLERY_IMAGE={IMAGE}", f"CUTLERY_DATA_DIR={d / 'data'}", f"CUTLERY_WORK_DIR={d / 'work'}",
        f"CUTLERY_MODELS_DIR={d / 'models'}", f"PUID={os.getuid()}", f"PGID={os.getgid()}", f"CUTLERY_PORT={PORT}",
        f"ANTHROPIC_API_KEY_FILE={key}", "CUTLERY_ANALYSIS_SETTLE_SECONDS=1", ""]))
    dc = ["docker", "compose", "--env-file", str(env), "-p", "cutlery-test-stack", "-f", str(ROOT / "compose.yaml")]
    pw = subprocess.run(dc + ["run", "--rm", "-T", "cutlery", "set-password", "--generate"], capture_output=True,
                        text=True, check=True).stdout
    password = next(l.split(":", 1)[1].strip() for l in pw.splitlines() if "password:" in l)
    subprocess.run(dc + ["up", "-d"], check=True, capture_output=True)
    s = {"dir": d, "dc": dc, "auth": "Basic " + base64.b64encode(f"cutlery:{password}".encode()).decode()}
    try:
        end = time.time() + 120
        while time.time() < end:
            try:
                if call(s, "GET", "/api/status")[0] == 200 and _healthy("cutlery-test-stack-runner-1"):
                    break
            except OSError:
                pass
            time.sleep(1)
        yield s
    finally:
        subprocess.run(dc + ["down"], capture_output=True)


def _healthy(name):
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", name], capture_output=True, text=True)
    return r.stdout.strip() == "healthy"


def call(s, method, path, body=None, form=False):
    data, headers = None, {"authorization": s["auth"]}
    if body is not None:
        if form:
            data = "&".join(f"{k}={v}" for k, v in body.items()).encode()
            headers["content-type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=headers, method=method)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        r = urllib.request.build_opener(NoRedirect).open(req, timeout=30)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def project(s, name, scenario):
    status, _, headers = call(s, "POST", "/projects/new", {"name": name}, form=True)
    pid = headers["location"].split("/")[2]
    a = s["dir"] / "work" / "projects" / pid / "analysis"
    a.mkdir(parents=True)
    (a / "clips.json").write_text('[{"id": "C001"}]')
    (s["dir"] / "data" / "projects" / pid / ".fake-claude-scenario").write_text(scenario)
    assert call(s, "PUT", f"/api/projects/{pid}/briefing", {"prompt": "A test film."})[0] == 200
    return pid


def job(s, pid):
    return json.loads(call(s, "GET", f"/api/projects/{pid}/jobs")[1])["jobs"][0]


def wait(s, pid, *states, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        j = job(s, pid)
        if j["state"] in states:
            return j
        time.sleep(1)
    raise AssertionError(f"job is {job(s, pid)}")


def test_job_container_is_isolated(stack):
    pid = project(stack, "Probe", "probe")
    project(stack, "Other", "ok")
    assert call(stack, "POST", f"/api/projects/{pid}/jobs", {"kind": "draft"})[0] == 201
    j = wait(stack, pid, "done", "failed")
    assert j["state"] == "done", j
    facts = json.loads(j["message"])
    assert facts["uid"] == os.getuid() and facts["projects_seen"] == [pid]          # only its own project
    assert facts["docker_sock"] is False and facts["root_writable"] is False and facts["models_writable"] is False
    assert facts["project_writable"] is True
    assert facts["secret_var"] == ["ANTHROPIC_API_KEY"] and facts["secret_len"] == len("sk-ant-api-test-only")
    assert facts["direct_internet"] != "open"                                         # no route to the outside
    assert "403" in facts["proxy_denied"]
    assert facts["proxy_allowed"].startswith("HTTP/1.1 200")
    out = subprocess.run(["docker", "ps", "-a", "--filter", "label=cutlery.job", "-q"], capture_output=True, text=True)
    assert out.stdout.strip() == ""                                                   # job containers are removed


def test_web_app_has_neither_socket_nor_credential(stack):
    r = subprocess.run(["docker", "exec", "cutlery-test-stack-cutlery-1", "sh", "-c",
                        "test -e /var/run/docker.sock && echo socket; test -e /run/secrets && echo secrets; "
                        "env | grep -iE 'ANTHROPIC_API_KEY|OAUTH_TOKEN' || true"], capture_output=True, text=True)
    assert r.stdout.strip() == ""


def test_pause_and_resume(stack):
    pid = project(stack, "Pause", "usage_limit")
    call(stack, "POST", f"/api/projects/{pid}/jobs", {"kind": "draft"})
    j = wait(stack, pid, "paused", "failed", "done")
    assert (j["state"], j["reason"]) == ("paused", "usage_limit")
    (stack["dir"] / "data" / "projects" / pid / ".fake-claude-scenario").write_text("ok")
    assert call(stack, "POST", f"/api/jobs/{j['id']}/resume")[0] == 200
    j = wait(stack, pid, "done", "failed", "paused")
    assert (j["state"], j["attempt"]) == ("done", 2)
    assert (stack["dir"] / "data" / "projects" / pid / "cuts" / "cut1" / "edit.json").is_file()


def test_job_survives_a_restart_of_the_web_app(stack):
    pid = project(stack, "Restart", "slow")
    call(stack, "POST", f"/api/projects/{pid}/jobs", {"kind": "draft"})
    wait(stack, pid, "running")
    time.sleep(3)
    subprocess.run(["docker", "restart", "cutlery-test-stack-cutlery-1"], check=True, capture_output=True)
    end = time.time() + 60
    while time.time() < end:
        try:
            if call(stack, "GET", "/api/status")[0] == 200:
                break
        except OSError:
            time.sleep(1)
    assert job(stack, pid)["state"] == "running"
    assert wait(stack, pid, "done", "failed", "paused", timeout=120)["state"] == "done"
