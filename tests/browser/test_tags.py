"""Browser test: tags on the material page – the filter with what is in the picture, a tag in the source monitor for
a range (key T), several clips chosen with Ctrl-click and tagged at once; the parts of the analysis on the setup page."""
import json
import pathlib
import shutil
import sys

import pytest

from test_material import make, strip
from test_upload import new_project

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "unit"))
from test_sidecar import variant  # noqa: E402

pytestmark = pytest.mark.browser


def found(n, tag, obj=None):
    return {"duration": float(n), "tags_key": "k", "usable": [], "flaws": [], "camera": [],
            "per_second": [{"t": i, "tags": [[tag(i), 0.9]], **({"objects": {obj: 1}} if obj else {})} for i in range(n)]}


def test_tags(page, server):
    if page.browser_name != "chrome" or not shutil.which("ffmpeg"):
        pytest.skip("one browser is enough; needs ffmpeg")
    pid = new_project(page, server)
    cfg = server["cfg"]
    a = cfg.work_dir / "projects" / pid / "analysis"
    media = cfg.work_dir / "projects" / pid / "media"
    specs = [("C001", "2012-07-14 09-00-00 A.MP4", lambda i: "desert", "dog"),
             ("C002", "2012-07-14 10-00-00 B.MP4", lambda i: "desert" if i < 3 else "road", None),
             ("C003", "2012-07-14 11-00-00 C.MP4", lambda i: "campfire", None)]
    entries = []
    (a / "image").mkdir(parents=True)
    for i, (cid, rel, tag, obj) in enumerate(specs, 1):
        f = media / (rel + ".mp4")
        make(f, "640x360", 6, "red")
        strip(f, a / "strips" / f"{cid}.jpg")
        entries.append({"id": cid, "rel_path": rel, "file": rel, "created": rel[:10] + " " + rel[11:19].replace("-", ":"),
                        "duration": 6.0, "width": 640, "height": 360})
        (a / "image" / f"{cid}.json").write_text(json.dumps(found(6, tag, obj)))
        sc = variant(rel_path=rel, file=rel, fingerprint__head_sha256=str(i) * 64)
        assert page.evaluate("([pid, sc]) => fetch(`/api/projects/${pid}/clips`, {method: 'POST', headers: "
                             "{'content-type': 'application/json'}, body: JSON.stringify(sc)}).then(r => r.status)",
                             [pid, sc]) == 200
    (a / "clips.json").write_text(json.dumps(entries))

    page.goto(f"{server['url']}/projects/{pid}/material")
    page.wait_for_selector(".tile")
    assert page.inner_text(".tile[data-id=C003] .tile-badges") .strip() == "Lagerfeuer"

    # the filter: what is in the picture, with how many clips; chosen tags must all be there
    page.click("#m-tags-btn")
    chips = page.eval_on_selector_all("#m-tags-list .tag-chip", "els => els.map(e => e.textContent)")
    assert "Wüste2" in chips and "Lagerfeuer1" in chips and "Hund1" in chips
    page.click("#m-tags-list .tag-chip:has-text('Wüste')")
    page.keyboard.press("Escape")
    assert page.is_hidden("#m-tags-pop")
    assert page.eval_on_selector_all(".tile", "els => els.map(e => e.dataset.id)") == ["C001", "C002"]
    assert page.inner_text("#m-tags-n") == "1" and "Wüste" in page.inner_text("#m-active-tags")
    page.click("#m-tags-btn")
    page.fill("#m-tags-search", "hu")
    assert page.eval_on_selector_all("#m-tags-list .tag-chip", "els => els.length") == 1
    page.click("#m-tags-list .tag-chip")
    page.keyboard.press("Escape")
    assert page.eval_on_selector_all(".tile", "els => els.map(e => e.dataset.id)") == ["C001"]
    page.click("#m-active-tags .link-button")                                  # clear all
    assert page.locator(".tile").count() == 3

    # the monitor: found tags with their time, a tag of one's own for a range
    page.click(".tile[data-id=C002]")
    page.wait_for_selector("#mon-atags .tag-chip")
    assert page.inner_text("#mon-atags").count("s") >= 2
    page.hover("#mon-atags .tag-chip:has-text('Straße')")
    assert page.locator("#mon-hl .hl").count() == 1
    page.evaluate("() => { const v = document.getElementById('mon-video'); v.currentTime = 1; }")
    page.keyboard.press("i")
    page.evaluate("() => { const v = document.getElementById('mon-video'); v.currentTime = 4; }")
    page.keyboard.press("o")
    assert "1:" not in page.inner_text("#mon-tag-scope") and "0:01" in page.inner_text("#mon-tag-scope")
    page.keyboard.press("t")
    assert page.evaluate("() => document.activeElement.id") == "mon-tag"
    page.keyboard.type("Sandsturm")
    page.keyboard.press("Enter")
    page.wait_for_selector("#mon-utags .tag-chip")
    assert "Sandsturm · 0:01.0–0:04.0" in page.inner_text("#mon-utags")
    assert page.locator("#mon-tagl .msel.tag").count() == 1
    page.hover("#mon-atags .tag-chip:has-text('Straße')")
    page.keyboard.press("Escape")
    assert "Sandsturm" in page.inner_text(".tile[data-id=C002] .tile-badges")

    # several clips at once
    page.click(".tile[data-id=C001]", modifiers=["Control"])
    page.click(".tile[data-id=C003]", modifiers=["Control"])
    assert page.is_visible("#m-selbar") and "2" in page.inner_text("#m-sel-n")
    page.fill("#m-sel-tag", "Tag eins")
    page.press("#m-sel-tag", "Enter")
    page.wait_for_function("() => document.querySelectorAll('.badge.utag').length >= 3")
    sel = json.loads((cfg.data_dir / "projects" / pid / "selects.json").read_text())["selects"]
    assert sorted((s["clip"], s["tag"], s["in"]) for s in sel) == [
        ("C001", "Tag eins", None), ("C002", "Sandsturm", 1.0), ("C003", "Tag eins", None)]
    page.click("#m-sel-clear")
    assert page.is_hidden("#m-selbar")
    page.click("#m-tags-btn")
    assert "Tag eins\n2" in page.inner_text("#m-tags-list")

    # managing: an own tag renamed, removed from chosen clips; a wrong automatic tag hidden (clip, everywhere)
    page.keyboard.press("Escape")
    page.click("#m-tags-btn")
    page.click("#m-tags-edit")
    page.once("dialog", lambda d: d.accept("Sandsturm groß"))
    page.click("#m-tags-list .tag-chip.editing:has-text('Sandsturm') button[title*='umbenennen']")
    page.wait_for_selector("#m-tags-list .tag-chip.editing:has-text('Sandsturm groß')")
    page.click("#m-tags-list .tag-chip.editing:has-text('Lagerfeuer') button")
    page.wait_for_selector("#m-tags-list .hidden-tag:has-text('Lagerfeuer')")
    assert "Lagerfeuer" not in page.inner_text(".tile[data-id=C003] .tile-badges")
    page.click("#m-tags-list .hidden-tag")                                     # shown again
    page.wait_for_function("() => document.querySelector('.tile[data-id=C003] .tile-badges').textContent.includes('Lagerfeuer')")
    page.click("#m-tags-edit")
    page.keyboard.press("Escape")
    page.click(".tile[data-id=C001]", modifiers=["Control"])
    page.fill("#m-sel-tag", "Tag eins")
    page.click("#m-sel-untag")
    page.wait_for_function("() => !document.querySelector('.tile[data-id=C001] .tile-badges').textContent.includes('Tag eins')")
    assert "Tag eins" in page.inner_text(".tile[data-id=C003] .tile-badges")
    page.click("#m-sel-clear")
    page.click(".tile[data-id=C001]")
    page.wait_for_selector("#mon-atags .tag-pair")
    page.click("#mon-atags .tag-pair:has-text('Hund') .tag-hide")
    page.wait_for_function("() => !document.getElementById('mon-atags').textContent.includes('Hund')")
    page.keyboard.press("Escape")
    hidden = json.loads((cfg.data_dir / "projects" / pid / "tags_hidden.json").read_text())
    assert hidden == {"all": [], "clips": {"C001": ["o:dog"]}}
    tags = sorted({s["tag"] for s in json.loads((cfg.data_dir / "projects" / pid / "selects.json").read_text())["selects"]})
    assert tags == ["Sandsturm groß", "Tag eins"]

    # what the analysis looks at, on the setup page
    page.goto(f"{server['url']}/projects/{pid}/setup")
    page.click("#parts-box summary")
    page.uncheck("input[data-part=transcripts]")
    page.click("#parts-form button[type=submit]")
    page.wait_for_function("() => document.getElementById('parts-status').textContent.length > 0")
    assert "5" in page.inner_text("#parts-count")
    assert 'parts = ["faces", "objects", "tags", "sound", "scenes"]' in (
        cfg.data_dir / "projects" / pid / "cutter.toml").read_text()
