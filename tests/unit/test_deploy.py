"""The deployment files agree with each other: compose.yaml, .env.example, Dockerfile, examples, versions."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import cutlery

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose():
    if not shutil.which("docker"):
        pytest.skip("needs docker compose to read compose.yaml")
    r = subprocess.run(["docker", "compose", "--env-file", str(ROOT / ".env.example"), "-f", str(ROOT / "compose.yaml"),
                        "config", "--format", "json"], capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        pytest.skip(f"docker compose config failed: {r.stderr[-300:]}")
    return json.loads(r.stdout)


def test_every_setting_of_compose_is_explained_in_env_example():
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", (ROOT / "compose.yaml").read_text()))
    example = (ROOT / ".env.example").read_text()
    explained = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", example, re.M))
    missing = used - explained - {"CUTLERY_IMAGE"}
    assert not missing, f"not in .env.example: {sorted(missing)}"


def test_storage_is_bind_mounts_only(compose):
    assert not compose.get("volumes"), "no named Docker volumes"
    for name, svc in compose["services"].items():
        for v in svc.get("volumes", []):
            assert v["type"] == "bind", (name, v)
            if v["target"] in ("/data", "/work", "/models"):
                assert v["bind"].get("create_host_path") is False, (name, v)


def test_services_restart_check_their_health_and_rotate_logs(compose):
    for name, svc in compose["services"].items():
        assert svc["restart"] == "unless-stopped", name
        assert svc["logging"]["options"]["max-size"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
    assert compose["services"]["runner"]["healthcheck"]["test"]
    # the web app's health check comes from the image (Dockerfile HEALTHCHECK)
    assert "HEALTHCHECK" in (ROOT / "Dockerfile").read_text()


def test_only_the_runner_has_the_socket_and_the_credential(compose):
    web, runner = compose["services"]["cutlery"], compose["services"]["runner"]
    targets = lambda svc: {v["target"] for v in svc.get("volumes", [])}
    assert "/var/run/docker.sock" not in targets(web) and "/var/run/docker.sock" in targets(runner)
    assert not any(t.startswith("/run/secrets") for t in targets(web))
    assert "/secrets" in targets(runner) and "/secrets" not in targets(web)     # tokens entered in the web UI
    assert not any(k in web["environment"] for k in ("CLAUDE_CODE_OAUTH_TOKEN_FILE", "ANTHROPIC_API_KEY_FILE"))
    assert web["cap_drop"] == ["ALL"]
    assert web["ports"][0]["host_ip"] == "127.0.0.1"


def test_cutter_release_is_the_same_for_the_image_and_for_development():
    dev = re.search(r"cutter@v([0-9.]+)", (ROOT / "requirements-dev.txt").read_text()).group(1)
    image = re.search(r"^ARG CUTTER_IMAGE=\S*cutter:([0-9.]+)@sha256:[0-9a-f]{64}$",
                      (ROOT / "Dockerfile").read_text(), re.M).group(1)
    assert dev == image


def test_version_has_one_source():
    assert 'dynamic = ["version"]' in (ROOT / "pyproject.toml").read_text()
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev\d+)?", cutlery.__version__)


def test_proxy_examples_point_to_the_default_port_and_pass_the_client_address():
    port = re.search(r"^CUTLERY_PORT=(\d+)", (ROOT / ".env.example").read_text(), re.M).group(1)
    nginx = (ROOT / "examples" / "nginx.conf").read_text()
    caddy = (ROOT / "examples" / "Caddyfile").read_text()
    assert f"proxy_pass http://127.0.0.1:{port};" in nginx and "X-Real-IP $remote_addr" in nginx
    assert f"reverse_proxy 127.0.0.1:{port}" in caddy and "X-Real-IP {remote_host}" in caddy
    chunk = int(re.search(r"const CHUNK = (\d+) \* 1024 \* 1024", (ROOT / "src/cutlery/static/js/footage.js").read_text()).group(1))
    assert int(re.search(r"client_max_body_size (\d+)m;", nginx).group(1)) > chunk


def test_fail2ban_filter_matches_the_log_line():
    from cutlery import auth
    src = pathlib.Path(auth.__file__).read_text()
    assert 'log.warning("cutlery: login failed from %s", ip)' in src
    conf = (ROOT / "examples" / "fail2ban" / "filter.d" / "cutlery.conf").read_text()
    assert "failregex = cutlery: login failed from <ADDR>" in conf
