"""Browser test: sharing a project – the owner makes a link, a friend opens it in another browser (no login), watches
and comments in the review, marks a favourite in the footage; the owner takes the suggestion over."""
import json
import shutil
import subprocess

import pytest

from conftest import new_project_api
from test_review_page import TIMELINE
from test_tags import found
from test_material import make, strip

pytestmark = pytest.mark.browser
pytest.importorskip("playwright.sync_api")


def test_share_with_a_friend(page, server, clips):
    if page.browser_name != "chrome" or not shutil.which("ffmpeg"):
        pytest.skip("one browser is enough; needs ffmpeg")
    cfg = server["cfg"]
    pid = new_project_api(page, server, "Namibia")
    d = cfg.work_dir / "projects" / pid / "review" / "cuts" / "cut1"
    d.mkdir(parents=True)
    (d / "timeline.json").write_text(json.dumps(TIMELINE))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=30",
                    "-c:v", "libx264", "-movflags", "+faststart", str(d / "review.mp4")], check=True)
    (cfg.data_dir / "projects" / pid / "cuts" / "cut1").mkdir(parents=True)
    (cfg.data_dir / "projects" / pid / "cuts" / "cut1" / "edit.json").write_text("{}")
    a = cfg.work_dir / "projects" / pid / "analysis"
    rel = "2012-07-14 09-00-00 A.MP4"
    make(cfg.work_dir / "projects" / pid / "media" / (rel + ".mp4"), "640x360", 6, "red")
    strip(cfg.work_dir / "projects" / pid / "media" / (rel + ".mp4"), a / "strips" / "C001.jpg")
    (a / "image").mkdir(parents=True)
    (a / "image" / "C001.json").write_text(json.dumps(found(6, lambda i: "desert")))
    (a / "clips.json").write_text(json.dumps([{"id": "C001", "rel_path": rel, "file": rel, "created": "2012-07-14 09:00:00",
                                               "duration": 6.0, "width": 640, "height": 360}]))
    from test_sidecar import variant
    assert page.evaluate("([pid, sc]) => fetch(`/api/projects/${pid}/clips`, {method: 'POST', headers: "
                         "{'content-type': 'application/json'}, body: JSON.stringify(sc)}).then(r => r.status)",
                         [pid, variant(rel_path=rel, file=rel, fingerprint__head_sha256="1" * 64)]) == 200

    # the owner makes a link
    page.goto(f"{server['url']}/projects/{pid}/share")
    page.fill("#share-name", "Alex")
    page.click("#share-form button[type=submit]")
    page.wait_for_selector("#share-made:not([hidden])")
    link = page.input_value("#share-url")
    assert "/s/" in link and page.locator("#share-table tbody tr").count() == 1

    # the friend: another browser without the login
    guest_ctx = page.context.browser.new_context()
    g = guest_ctx.new_page()
    bad = []
    g.on("response", lambda r: bad.append((r.request.method, r.url, r.status)) if r.status >= 400 else None)
    g.goto(link.replace("http://testserver", server["url"]) if link.startswith("http://testserver") else link)
    g.wait_for_url(f"**/projects/{pid}/review")
    assert "Alex" in g.inner_text(".guest-badge")
    assert g.locator(".site-nav").count() == 0 and g.is_hidden("#review-done") and g.is_hidden("#next-note")
    frame = g.frame_locator("#review-frame")
    frame.locator("#note").wait_for()
    frame.locator("#note").fill("Mehr Wüste hier")
    frame.locator("#note").press("Enter")
    api = f"/projects/{pid}/review/api/entries?cut=cut1"
    got = {}
    for _ in range(40):
        got = g.evaluate(f"() => fetch('{api}').then(r => r.json())")
        if got.get("entries"):
            break
        g.wait_for_timeout(250)
    assert got["entries"] and got["entries"][0]["person"] == "Alex", (got, bad)
    # in the footage: a favourite is a suggestion
    g.click(".project-nav a[href$='/material']")
    g.wait_for_selector(".tile")
    assert g.is_hidden("#m-tags-edit") and g.locator("a[href$='/analysis']").count() == 0
    g.click(".tile[data-id=C001]")
    g.wait_for_selector("#monitor[open]")
    g.keyboard.press("f")
    g.wait_for_selector("#mon-marks .mark.suggested")
    g.keyboard.press("Escape")
    # what is not the guest's: back to the review
    g.goto(f"{server['url']}/settings")
    g.wait_for_url(f"**/projects/{pid}/review")
    guest_ctx.close()

    # the owner sees the suggestion and takes it over
    page.goto(f"{server['url']}/projects/{pid}/material")
    page.wait_for_selector("#m-sugg:not([hidden])")
    assert "Alex" in page.inner_text("#m-sugg") and "💡 1" in page.inner_text(".tile[data-id=C001] .tile-badges")
    page.click(".tile[data-id=C001]")
    page.wait_for_selector("#mon-sugg li.mark")
    page.click("#mon-sugg li.mark .ok")
    page.wait_for_function("() => document.getElementById('mon-sugg').hidden")
    sel = json.loads((cfg.data_dir / "projects" / pid / "selects.json").read_text())["selects"]
    assert [(s["clip"], s["kind"], s["note"]) for s in sel] == [("C001", "favorite", "(Alex)")]
    page.keyboard.press("Escape")
    assert page.is_hidden("#m-sugg")
