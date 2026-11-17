"""Browser test: the analysis page plays a clip's 360p copy and shows contact sheets in a lightbox."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from test_upload import new_project

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "unit"))
from test_sidecar import variant  # noqa: E402

pytestmark = pytest.mark.browser


def test_player_and_lightbox(page, server, clips):
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough")
    pid = new_project(page, server)
    cfg = server["cfg"]
    a = cfg.work_dir / "projects" / pid / "analysis"
    (a / "sheets").mkdir(parents=True)
    media = cfg.work_dir / "projects" / pid / "media"
    entries = []
    for i, name in enumerate(("Day 1/2024-06-01 10-15-00 h264.mp4", "Phone/vfr.mp4"), 1):
        cid = f"C{i:03d}"
        dst = media / (name + ".mp4")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(clips / name, dst)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(dst), "-frames:v", "1", "-vf", "scale=640:-2",
                        str(a / "sheets" / f"{cid}.jpg")], check=True)
        entries.append({"id": cid, "rel_path": name, "file": name.split("/")[-1], "duration": 2})
        sc = variant(rel_path=name, file=name.split("/")[-1], fingerprint__head_sha256=f"{i}" * 64)
        r = page.evaluate("([pid, sc]) => fetch(`/api/projects/${pid}/clips`, {method: 'POST', "
                          "headers: {'content-type': 'application/json'}, body: JSON.stringify(sc)}).then(r => r.status)",
                          [pid, sc])
        assert r == 200
    (a / "clips.json").write_text(json.dumps(entries))
    page.goto(f"{server['url']}/projects/{pid}/analysis")

    page.click("[data-target='media-C001']")
    page.wait_for_function("() => { const v = document.querySelector('#media-C001 video'); return v && v.readyState >= 2; }")
    assert page.evaluate("() => document.querySelector('#media-C001 video').duration") > 1.5
    page.click("[data-target='media-C002']")                         # one plays at a time
    page.wait_for_function("() => document.querySelector('#media-C001 video').paused")

    page.click("a.js-lightbox[data-group='sheets'] >> nth=0")
    assert page.is_visible("#lightbox")
    assert page.get_attribute("#lightbox-img", "src").endswith("/sheets/C001.jpg")
    page.wait_for_function("() => document.getElementById('lightbox-img').naturalWidth > 0")
    if os.environ.get("CUTLERY_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["CUTLERY_SCREENSHOTS"], "lightbox.png"))
    page.keyboard.press("ArrowRight")
    assert page.get_attribute("#lightbox-img", "src").endswith("/sheets/C002.jpg")
    page.keyboard.press("Escape")
    assert page.is_hidden("#lightbox")
    if os.environ.get("CUTLERY_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["CUTLERY_SCREENSHOTS"], "analysis.png"), full_page=True)

    # delete a clip from here: its card goes, and stays gone after a reload (before the next analysis run)
    page.on("dialog", lambda d: d.accept())
    page.click(".clip-card:has(#media-C002) .js-delete")
    page.wait_for_function("() => !document.getElementById('media-C002')")
    assert page.inner_text("#filter-count").startswith("1")
    page.reload()
    assert page.locator(".clip-card").count() == 1
    clips_now = page.evaluate(f"() => fetch('/api/projects/{pid}/clips').then(r => r.json())")["clips"]
    assert [c["rel_path"] for c in clips_now] == [entries[0]["rel_path"]]
