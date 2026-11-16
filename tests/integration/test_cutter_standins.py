"""Integration: the real `cutter analyze` reads Cutlery's stand-ins and sidecars (needs Docker and a cutter image).

    CUTLERY_TEST_CUTTER_IMAGE=ghcr.io/aaronblankenburg/cutter:0.2.1 \
    CUTLERY_TEST_CUTTER_SRC=/path/to/cutter-checkout \
    pytest -m integration tests/integration

CUTLERY_TEST_CUTTER_SRC mounts a cutter checkout (branch cutlery-support) over the image's code, until the image
contains the stand-in support. Without the variables the tests are skipped.
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

from cutlery import cutter_project
from cutlery.db import Database
from cutlery.projects import Projects
from test_app import ffprobe_sidecar, make_standin

pytestmark = pytest.mark.integration
IMAGE = os.environ.get("CUTLERY_TEST_CUTTER_IMAGE")
SRC = os.environ.get("CUTLERY_TEST_CUTTER_SRC")


@pytest.fixture(scope="module", autouse=True)
def docker_available():
    if not IMAGE or not shutil.which("docker"):
        pytest.skip("set CUTLERY_TEST_CUTTER_IMAGE (and have Docker) to run the cutter integration tests")


def run_cutter(project_dir: pathlib.Path, *mounts: pathlib.Path, args=("analyze", "--no-transcripts", "--no-image")):
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", f"CUTTER_PROJECT={project_dir}",
           "-w", str(project_dir), "-e", "PYTHONDONTWRITEBYTECODE=1"]
    for m in {project_dir, *mounts}:
        cmd += ["-v", f"{m}:{m}"]
    if SRC:
        cmd += ["-v", f"{SRC}:/repo:ro", "-e", "PYTHONPATH=/repo/src"]
    cmd += ["--entrypoint", "python", IMAGE, "-m", "cutter", *args, "--json"]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_cutter_analyses_a_cutlery_project(cfg, clips, tmp_path):
    db = Database(cfg.database)
    projects = Projects(cfg, db)
    pid = projects.create("Integration")["id"]
    paths = projects.paths(pid)
    wanted = {"Day 1/2024-06-01 10-15-00 h264.mp4", "Day 2/ntsc 29.97.mov", "Phone/portrait rotated.mp4"}
    for rel in wanted:
        sc = ffprobe_sidecar(clips / rel, rel)
        # what the browser writes for the original: rotation is clockwise
        sc["video"]["rotation"] = 270 if "portrait" in rel else 0
        if "ntsc" in rel:
            sc["timecode"] = "01:02:03;04"
        projects.register(pid, sc)
        stand_in = paths.standin(rel)
        stand_in.parent.mkdir(parents=True, exist_ok=True)
        make_standin(clips / rel, stand_in)
    cutter_project.write(paths, "Integration")
    r = run_cutter(paths.data, paths.work, paths.sidecars)
    assert r.returncode == 0, r.stderr[-2000:]
    found = {c["file"]: c for c in cutter_project.clips_json(paths)}
    assert set(found) == {pathlib.PurePosixPath(rel).name for rel in wanted}, r.stderr[-1500:]
    ntsc = found["ntsc 29.97.mov"]
    assert (ntsc["width"], ntsc["height"], ntsc["fps_raw"], ntsc["timecode"]) == (1280, 720, "30000/1001", "01:02:03;04")
    assert ntsc["standin"] == {"width": 640, "height": 360}
    assert ntsc["path"].startswith(str(paths.media)) and ntsc["original"]["size"] == (clips / "Day 2/ntsc 29.97.mov").stat().st_size
    portrait = found["portrait rotated.mp4"]
    assert (portrait["rotation"], portrait["width"], portrait["height"]) == (90, 720, 1280)   # ffprobe's convention
    ids = {f: c["id"] for f, c in found.items()}
    # a second run keeps the IDs
    assert run_cutter(paths.data, paths.work, paths.sidecars).returncode == 0
    assert {c["file"]: c["id"] for c in cutter_project.clips_json(paths)} == ids
