"""Browser test: the material page – days, hover scrub, the source monitor with transcript, favourites, filters, list."""
import json
import os
import shutil
import subprocess

import pathlib
import sys

import pytest

from test_upload import new_project

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "unit"))
from test_sidecar import variant  # noqa: E402

pytestmark = pytest.mark.browser


def make(path, size, seconds, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:d={seconds}",
                    "-vf", f"drawbox=w=60:h=60:color={colour}:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-g", "25", str(path)], check=True)


def strip(video, target):
    """Five pictures side by side, 80 px high, 3 px apart (as cutter's film strips)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                    "fps=1,scale=-2:80,tile=5x1:padding=3:color=0x181818", "-frames:v", "1", str(target)], check=True)


def test_material_page(page, server):
    if page.browser_name != "chrome" or not shutil.which("ffmpeg"):
        pytest.skip("one browser is enough; needs ffmpeg")
    pid = new_project(page, server)
    cfg = server["cfg"]
    a = cfg.work_dir / "projects" / pid / "analysis"
    media = cfg.work_dir / "projects" / pid / "media"
    specs = [("C001", "Tag 2/2012-07-15 08-00-00 GOPR0001.MP4", "2012-07-15 08:00:00", "640x360", "red"),
             ("C002", "Tag 1/2012-07-14 18-30-00 IMG_0002.MOV", "2012-07-14 18:30:00", "360x640", "blue"),
             ("C003", "Tag 1/2012-07-14 09-00-00 DSC_0003.MOV", "2012-07-14 09:00:00", "640x360", "green")]
    entries = []
    for i, (cid, rel, created, size, colour) in enumerate(specs, 1):
        f = media / (rel + ".mp4")
        make(f, size, 6, colour)
        strip(f, a / "strips" / f"{cid}.jpg")
        w, h = map(int, size.split("x"))
        entries.append({"id": cid, "rel_path": rel, "file": rel.split("/")[-1], "created": created, "duration": 6.0,
                        "width": w, "height": h})
        sc = variant(rel_path=rel, file=rel.split("/")[-1], fingerprint__head_sha256=str(i) * 64)
        status = page.evaluate("([pid, sc]) => fetch(`/api/projects/${pid}/clips`, {method: 'POST', "
                               "headers: {'content-type': 'application/json'}, body: JSON.stringify(sc)}).then(r => r.status)",
                               [pid, sc])
        assert status == 200
    (a / "clips.json").write_text(json.dumps(entries))
    (a / "transcripts").mkdir()
    (a / "transcripts" / "C001.json").write_text(json.dumps({"language": "de", "segments": [
        {"start": 0.4, "end": 3.0, "text": "Guten Morgen aus der Wüste",
         "words": [[0.4, 0.8, " Guten", 1], [0.8, 1.3, " Morgen", 1], [1.3, 1.6, " aus", 1], [1.6, 1.9, " der", 1],
                   [1.9, 3.0, " Wüste", 1]]}]}))

    page.goto(f"{server['url']}/projects/{pid}/material")
    page.wait_for_selector(".tile")
    days = page.eval_on_selector_all(".day-head > span:first-child", "els => els.map(e => e.textContent)")
    assert len(days) == 2 and "2012" in days[0]
    order = page.eval_on_selector_all(".tile", "els => els.map(e => e.dataset.id)")
    assert order == ["C003", "C002", "C001"]                                   # by day, then by time
    assert page.inner_text("#m-days").count("·") == 2
    page.wait_for_function("() => [...document.querySelectorAll('.tile-frame')].every(f => f.style.backgroundImage.includes('/poster/'))")
    assert page.evaluate("() => document.querySelector('.tile[data-id=C002] .tile-frame').classList.contains('portrait')")

    # hover scrub: a video follows the pointer
    page.locator(".tile[data-id=C001]").scroll_into_view_if_needed()
    box = page.locator(".tile[data-id=C001] .tile-pic").bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2)
    page.mouse.move(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2, steps=4)
    page.wait_for_function("() => { const v = document.querySelector('.tile[data-id=C001] video'); "
                           "return v && v.classList.contains('ready') && v.currentTime > 3; }")
    page.mouse.move(0, 0)
    assert page.locator(".tile video").count() == 0

    # the monitor: the transcript jumps, I/O + F makes a favourite of the range
    page.click(".tile[data-id=C001]")
    page.wait_for_function("() => document.getElementById('monitor').open && document.getElementById('mon-video').readyState >= 2")
    page.wait_for_selector("#mon-transcript .word")
    page.click("#mon-transcript .word >> nth=4")
    page.wait_for_function("() => Math.abs(document.getElementById('mon-video').currentTime - 1.9) < 0.1")
    assert page.evaluate("() => document.querySelector('#mon-transcript .word.now').textContent") == " Wüste"
    page.keyboard.press("i")
    page.keyboard.press("Shift+ArrowRight")
    for _ in range(4):
        page.keyboard.press("Shift+ArrowRight")                                # 5 × 5 frames: 1 s
    page.keyboard.press("o")
    assert "0:01.9–0:02.9" in page.inner_text("#mon-range-text")
    page.fill("#mon-note", "Sonnenaufgang")
    page.click("#mon-video")                                                   # out of the field (and plays)
    page.keyboard.press("k")
    page.keyboard.press("f")
    page.wait_for_selector("#mon-marks .mark.favorite")
    sel = page.evaluate(f"() => fetch('/api/projects/{pid}/selects').then(r => r.json())")["selects"]
    assert len(sel) == 1 and sel[0]["clip"] == "C001" and sel[0]["kind"] == "favorite" and sel[0]["note"] == "Sonnenaufgang"
    assert abs(sel[0]["in"] - 1.9) < 0.05 and abs(sel[0]["out"] - 2.9) < 0.05
    page.keyboard.press("ArrowUp")                                             # previous clip in the list
    page.wait_for_function("() => document.getElementById('mon-title').textContent.includes('IMG_0002')")
    page.keyboard.press("x")                                                   # reject the whole clip
    page.wait_for_selector("#mon-marks .mark.reject")
    if os.environ.get("CUTLERY_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["CUTLERY_SCREENSHOTS"], "material_monitor.png"))
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.getElementById('monitor').open")

    # marks on the tiles, filters, the list
    assert page.inner_text(".tile[data-id=C001] .tile-badges").strip().endswith("★")
    page.select_option("#m-mark", "favorite")
    assert page.eval_on_selector_all(".tile", "els => els.map(e => e.dataset.id)") == ["C001"]
    page.select_option("#m-mark", "")
    page.fill("#m-search", "wüste")
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 1")
    page.fill("#m-search", "")
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 3")
    if os.environ.get("CUTLERY_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["CUTLERY_SCREENSHOTS"], "material.png"), full_page=True)
    page.click("#m-list")
    assert page.locator(".material-table tbody tr").count() == 3
    page.reload()
    page.wait_for_selector(".material-table")                                  # the view is remembered
    page.click("#m-grid")
    md = (cfg.data_dir / "projects" / pid / "briefing.md")
    assert not md.exists() or "Sonnenaufgang" in md.read_text()
