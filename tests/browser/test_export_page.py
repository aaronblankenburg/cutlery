"""Browser test of the export page: originals are checked by fingerprint without uploading; renamed files are
matched, missing ones reported; the package downloads (with the fake cutter of the unit tests)."""
import hashlib
import json
import shutil
import time
import zipfile

import pytest

from test_sidecar import variant
from conftest import new_project_api

pytestmark = pytest.mark.browser
pytest.importorskip("playwright.sync_api")


def fingerprint(path):
    data = path.read_bytes()
    return {"size": len(data), "head_sha256": hashlib.sha256(data[:1 << 20]).hexdigest(),
            "tail_sha256": hashlib.sha256(data[-(1 << 20):]).hexdigest()}


def test_check_originals_and_download(page, server, clips, tmp_path):
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough")
    cfg = server["cfg"]
    names = ["Day 1/2024-06-01 10-15-00 h264.mp4", "Day 2/ntsc 29.97.mov", "Day 2/start timecode.mov"]
    footage = tmp_path / "Island 2026"
    pid = new_project_api(page, server, "Island 2026")
    for rel in names:
        sc = variant(rel_path=rel, file=rel.split("/")[-1], fingerprint=fingerprint(clips / rel),
                     size=(clips / rel).stat().st_size)
        r = page.request.post(f"{server['url']}/api/projects/{pid}/clips", data=json.dumps(sc),
                              headers={"content-type": "application/json"})
        assert r.ok, r.text()
        cid = r.json()["id"]
        server["app"].state.projects.mark(pid, cid, "uploaded")
    (cfg.data_dir / "projects" / pid / "cuts" / "cut1").mkdir(parents=True)
    (cfg.data_dir / "projects" / pid / "cuts" / "cut1" / "edit.json").write_text("{}")
    # on the user's computer: one file renamed, one gone
    (footage / "Day 1").mkdir(parents=True)
    (footage / "Day 2").mkdir(parents=True)
    shutil.copy(clips / names[0], footage / "Day 1" / "2024-06-01 10-15-00 h264.mp4")
    shutil.copy(clips / names[1], footage / "Day 2" / "ntsc renamed.mov")

    page.goto(f"{server['url']}/projects/{pid}/export")
    page.check("input[name=os][value=windows]", force=True)
    page.fill("#root", "D:\\Videos\\Island 2026")
    page.set_input_files("#check-input", str(footage))
    page.wait_for_function("() => window.cutleryExport && window.cutleryExport.result")
    result = page.evaluate("() => window.cutleryExport.result")
    assert (result["found"], result["renamed"], result["missing"]) == (1, 1, 1)
    assert result["relink"] == {"Day 2/ntsc 29.97.mov": "Day 2/ntsc renamed.mov"}
    lines = page.inner_text("#check-lines")
    assert "Day 2\\ntsc 29.97.mov → Day 2\\ntsc renamed.mov" in lines
    assert page.locator("#root-mismatch").is_hidden()          # path and folder name agree

    with page.expect_download() as dl:
        page.click("#download")
    path = dl.value.path()
    z = zipfile.ZipFile(path)
    timeline = json.loads(z.read("Film_cut1.xml"))               # what the fake cutter got
    assert timeline == {"os": "windows", "root": "D:\\Videos\\Island 2026", "relink": result["relink"], "to": "premiere"}
    assert dl.value.suggested_filename == "Island_2026_cut1_premiere.zip"
