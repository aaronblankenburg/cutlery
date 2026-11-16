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
    assert abs(ntsc["duration"] - 2.0) < 0.05
    assert ntsc["path"].startswith(str(paths.media)) and ntsc["original"]["size"] == (clips / "Day 2/ntsc 29.97.mov").stat().st_size
    portrait = found["portrait rotated.mp4"]
    assert (portrait["rotation"], portrait["width"], portrait["height"]) == (90, 720, 1280)   # ffprobe's convention
    ids = {f: c["id"] for f, c in found.items()}
    # a second run keeps the IDs
    assert run_cutter(paths.data, paths.work, paths.sidecars).returncode == 0
    assert {c["file"]: c["id"] for c in cutter_project.clips_json(paths)} == ids


def test_premiere_package_points_to_the_originals(cfg, clips, tmp_path, monkeypatch):
    """Analysis + edit list + Cutlery's export path with the real cutter: the XML names the originals, not the copies."""
    import dataclasses
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    from cutlery import export

    db = Database(cfg.database)
    projects = Projects(cfg, db)
    pid = projects.create("Island 2026")["id"]
    paths = projects.paths(pid)
    wanted = {"Day 2/ntsc 29.97.mov": "01:02:03;04", "Day 2/start timecode.mov": "10:00:00:00"}
    for rel, tc in wanted.items():
        sc = ffprobe_sidecar(clips / rel, rel)
        sc["timecode"] = tc
        projects.register(pid, sc)
        stand_in = paths.standin(rel)
        stand_in.parent.mkdir(parents=True, exist_ok=True)
        make_standin(clips / rel, stand_in)
    cutter_project.write(paths, "Island 2026")
    assert run_cutter(paths.data, paths.work, paths.sidecars).returncode == 0
    ids = {c["rel_path"]: c["id"] for c in cutter_project.clips_json(paths)}
    edit = {"title": "Island 2026 (cut1)", "fps": 25, "music": [], "sections": [{"name": "Start", "shots": [
        {"nr": 1, "clip": ids["Day 2/start timecode.mov"], "in": 0.2, "out": 1.4, "kind": "picture", "note": ""},
        {"nr": 2, "clip": ids["Day 2/ntsc 29.97.mov"], "in": 0.5, "out": 1.5, "kind": "picture", "note": ""}]}]}
    (paths.data / "cuts" / "cut1").mkdir(parents=True)
    (paths.data / "cuts" / "cut1" / "edit.json").write_text(json.dumps(edit))
    monkeypatch.setenv("CUTLERY_TEST_MOUNTS", f"{cfg.data_dir}:{cfg.work_dir}")
    wrapper = str(pathlib.Path(__file__).with_name("docker_cutter.sh"))
    cfg2 = dataclasses.replace(cfg, cutter_bin=wrapper)
    req = export.check_request({"os": "windows", "root": "D:\\Videos\\Island 2026",
                                "relink": json.dumps({"Day 2/ntsc 29.97.mov": "Day 2/renamed ntsc.mov"})}, ["cut1"])
    zip_path = export.build(cfg2, paths, "Island 2026", req, "de")
    z = zipfile.ZipFile(io.BytesIO(zip_path.read_bytes()))
    zip_path.unlink()
    xml_name = next(n for n in z.namelist() if n.endswith(".xml"))
    root = ET.fromstring(z.read(xml_name))
    files = {f.findtext("name"): f for f in root.iter("file") if f.find("pathurl") is not None}
    tc = files["start timecode.mov"]
    assert tc.findtext("pathurl") == "file://localhost/D:/Videos/Island%202026/Day%202/start%20timecode.mov"
    assert tc.findtext("timecode/string") == "10:00:00:00"
    assert tc.findtext("media/video/samplecharacteristics/width") == "1280"      # the original's, not 640
    ntsc = files["renamed ntsc.mov"]
    assert ntsc.findtext("pathurl").endswith("/Day%202/renamed%20ntsc.mov")
    assert (ntsc.findtext("rate/timebase"), ntsc.findtext("rate/ntsc"), ntsc.findtext("timecode/displayformat")) == \
        ("30", "TRUE", "DF")
    assert not any("/projects/" in f.findtext("pathurl") for f in files.values())   # never the server's copies
    assert "IMPORT-PREMIERE.txt" in z.namelist()

    # the same cut for Final Cut Pro and for FilmCraft
    req = export.check_request({"os": "macos", "root": "/Volumes/Card/Island 2026", "format": "fcp"}, ["cut1"])
    zip_path = export.build(cfg2, paths, "Island 2026", req, "en")
    z = zipfile.ZipFile(io.BytesIO(zip_path.read_bytes()))
    zip_path.unlink()
    fx = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".fcpxml"))))
    assets = {a.get("name"): a for a in fx.iter("asset")}
    a = assets["start timecode.mov"]
    assert a.find("media-rep").get("src") == "file:///Volumes/Card/Island%202026/Day%202/start%20timecode.mov"
    assert a.get("start") == "36000s"
    fmt = next(f for f in fx.iter("format") if f.get("id") == a.get("format"))
    assert fmt.get("width") == "1280"
    assert "IMPORT-FINAL-CUT.txt" in z.namelist()

    req = export.check_request({"os": "windows", "root": "D:\\Videos\\Island 2026", "format": "filmcraft"}, ["cut1"])
    zip_path = export.build(cfg2, paths, "Island 2026", req, "de")
    z = zipfile.ZipFile(io.BytesIO(zip_path.read_bytes()))
    zip_path.unlink()
    project = json.loads(z.read(next(n for n in z.namelist() if n.endswith(".fcproj"))))
    media = [i["kind"]["Media"]["media"]["File"]["path"] for i in project["project"]["items"].values()
             if "File" in (i["kind"].get("Media") or {}).get("media", {})]
    assert sorted(media) == ["D:/Videos/Island 2026/Day 2/ntsc 29.97.mov", "D:/Videos/Island 2026/Day 2/start timecode.mov"]
    assert "IN-FILMCRAFT-OEFFNEN.txt" in z.namelist()

    # on the server the timeline plays the stand-ins: scaled for their size (640 wide), not the originals'
    assert run_cutter(paths.data, paths.work, paths.sidecars, args=("timeline", "cut1")).returncode == 0
    local = ET.parse(next((paths.work / "cuts").rglob("cut1.fcp7.xml"))).getroot()
    scales = {float(p.findtext("value")) for p in local.iter("parameter") if p.findtext("parameterid") == "scale"}
    assert scales == {300.0}
