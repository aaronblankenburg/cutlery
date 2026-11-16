import base64
import pathlib
import shutil
import subprocess

import pytest

from cutlery import config as config_mod

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GENERATED = FIXTURES / "generated" / "footage"
PASSWORD = "test-password-123"


def auth_header(user="cutlery", password=PASSWORD):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def cfg(tmp_path):
    for d in ("data", "work", "models"):
        (tmp_path / d).mkdir()
    return config_mod.load({"CUTLERY_DATA_DIR": str(tmp_path / "data"), "CUTLERY_WORK_DIR": str(tmp_path / "work"),
                            "CUTLERY_MODELS_DIR": str(tmp_path / "models")})


@pytest.fixture
def app(cfg):
    from cutter.review_app import set_password

    from cutlery.app import create_app

    set_password.store(str(cfg.htpasswd), "cutlery", PASSWORD)
    return create_app(cfg, health_token="health-token")


@pytest.fixture
def client(app):
    from starlette.testclient import TestClient

    with TestClient(app, base_url="http://testserver", headers=auth_header()) as c:
        yield c


@pytest.fixture(scope="session")
def clips():
    """The generated test clips (made once with ffmpeg)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    subprocess.run(["python3", str(FIXTURES / "make_clips.py")], check=True, capture_output=True)
    return GENERATED
