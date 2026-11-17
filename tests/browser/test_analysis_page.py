"""Browser test: the analysis page plays a clip's 360p copy and shows contact sheets in a lightbox."""
import json
import os
import shutil
import subprocess

import pytest

from test_upload import new_project

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
