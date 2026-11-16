"""The whole stack without a credential in .env: jobs wait, a token entered in the web UI reaches the job container
and nothing else (see test_stack.py for how to build the images).

    CUTLERY_TEST_STACK_IMAGE=cutlery:fakeclaude pytest -m integration tests/integration/test_stack_token.py
"""
import base64
import json
import os
import shutil
import subprocess
import time

import pytest

import test_stack
from test_stack import IMAGE, ROOT, _healthy

pytestmark = pytest.mark.integration
PORT = 8295
TOKEN = "sk-ant-oat01-" + "Zq9_xY-" * 10


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if not IMAGE or not shutil.which("docker"):
        pytest.skip("set CUTLERY_TEST_STACK_IMAGE (an image with the simulated claude) to run the stack tests")
    d = tmp_path_factory.mktemp("stack-token")
    for sub in ("data", "work", "models", "stored"):
        (d / sub).mkdir()
    (d / "stored").chmod(0o700)
    env = d / ".env"
    env.write_text("\n".join([
        f"CUTLERY_IMAGE={IMAGE}", f"CUTLERY_DATA_DIR={d / 'data'}", f"CUTLERY_WORK_DIR={d / 'work'}",
        f"CUTLERY_MODELS_DIR={d / 'models'}", f"CUTLERY_SECRETS_DIR={d / 'stored'}", f"PUID={os.getuid()}",
        f"PGID={os.getgid()}", f"CUTLERY_PORT={PORT}", "CUTLERY_ANALYSIS_SETTLE_SECONDS=1", ""]))
    dc = ["docker", "compose", "--env-file", str(env), "-p", "cutlery-test-token", "-f", str(ROOT / "compose.yaml")]
    pw = subprocess.run(dc + ["run", "--rm", "--no-deps", "-T", "cutlery", "set-password", "--generate"],
                        capture_output=True, text=True, check=True).stdout
    password = next(l.split(":", 1)[1].strip() for l in pw.splitlines() if "password:" in l)
    subprocess.run(dc + ["up", "-d"], check=True, capture_output=True)
    s = {"dir": d, "dc": dc, "auth": "Basic " + base64.b64encode(f"cutlery:{password}".encode()).decode(),
         "web": "cutlery-test-token-cutlery-1"}
    old_port = test_stack.PORT
    test_stack.PORT = PORT
    try:
        end = time.time() + 120
        while time.time() < end:
            try:
                if test_stack.call(s, "GET", "/api/status")[0] == 200 and _healthy("cutlery-test-token-runner-1"):
                    break
            except OSError:
                pass
            time.sleep(1)
        yield s
    finally:
        test_stack.PORT = old_port
        subprocess.run(dc + ["down"], capture_output=True)


def test_jobs_wait_until_a_token_is_entered_in_the_web_ui(stack):
    call = test_stack.call
    page = call(stack, "GET", "/")[1].decode()
    assert "No Claude access is set up yet" in page
    pid = test_stack.project(stack, "Probe", "probe")
    assert call(stack, "POST", f"/api/projects/{pid}/jobs", {"kind": "draft"})[0] == 201
    j = test_stack.wait(stack, pid, "paused", "failed", "done")
    assert (j["state"], j["reason"]) == ("paused", "no_credential")

    assert call(stack, "PUT", "/api/claude/token", {"token": "not a token"})[0] == 400
    assert call(stack, "PUT", "/api/claude/token", {"token": TOKEN})[0] == 200
    info = json.loads(call(stack, "GET", "/api/claude")[1])
    assert info["kind"] == "oauth" and info["source"] == "stored" and TOKEN not in json.dumps(info)
    stored = stack["dir"] / "stored" / "claude-oauth-token"
    assert stored.read_text().strip() == TOKEN and stored.stat().st_uid == os.getuid()
    assert (stored.stat().st_mode & 0o777) == 0o600
    assert TOKEN not in call(stack, "GET", "/settings")[1].decode()

    # the web container cannot see it
    r = subprocess.run(["docker", "exec", "cutlery-test-token-cutlery-1", "sh", "-c",
                        f"grep -rl '{TOKEN[:20]}' /data /work /tmp 2>/dev/null; test -e /secrets && echo mounted; true"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == ""

    assert call(stack, "POST", f"/api/jobs/{j['id']}/resume")[0] == 200
    j = test_stack.wait(stack, pid, "done", "failed", "paused")
    assert j["state"] == "done", j
    facts = json.loads(j["message"])
    assert facts["secret_var"] == ["CLAUDE_CODE_OAUTH_TOKEN"] and facts["secret_len"] == len(TOKEN)

    # removing it makes the next job wait again
    assert call(stack, "DELETE", "/api/claude/token")[0] == 200
    assert not stored.exists()
