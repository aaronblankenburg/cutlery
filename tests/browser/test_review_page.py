"""Browser test of the review page: cutter's review app in the frame, a comment at a moment, finishing the review
with notes for the next cut."""
import json
import subprocess

import pytest

from conftest import new_project_api
TIMELINE = {"format": 2, "version": "cut1", "title": "Island (cut1)", "fps": 25, "duration": 30.0,
            "sections": [{"name": "Arrival", "start": 0.0, "end": 12.0}, {"name": "Glacier", "start": 12.0, "end": 30.0}],
            "shots": [{"nr": 1, "section": "Arrival", "clip": "C001", "start": 0.0, "end": 12.0, "source_in": 1.0},
                      {"nr": 2, "section": "Glacier", "clip": "C002", "start": 12.0, "end": 30.0, "source_in": 0.0}],
            "edit_points": [{"t": 12.0, "before": 1, "after": 2}], "music": [], "subtitles": [],
            "decisions": {"entries": [{"id": "len", "kind": "assumption", "title": "Length", "t": 5.0, "text": "About 5 min."}]},
            "video": {"file": "review.mp4"}}

pytestmark = pytest.mark.browser
pytest.importorskip("playwright.sync_api")


def test_comment_and_finish_the_review(page, server, clips, monkeypatch):
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")
    cfg = server["cfg"]
    pid = new_project_api(page, server, "Island 2026")
    d = cfg.work_dir / "projects" / pid / "review" / "cuts" / "cut1"
    d.mkdir(parents=True)
    (d / "timeline.json").write_text(json.dumps(TIMELINE))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=30",
                    "-f", "lavfi", "-i", "sine=duration=30", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                    "-movflags", "+faststart", str(d / "review.mp4")], check=True)
    (cfg.data_dir / "projects" / pid / "cuts" / "cut1").mkdir(parents=True)
    (cfg.data_dir / "projects" / pid / "cuts" / "cut1" / "edit.json").write_text("{}")

    page.goto(f"{server['url']}/projects/{pid}/review")
    page.wait_for_function("() => window.cutleryReview && window.cutleryReview.shown === 'cut1'")   # the frame said so
    box = page.locator("#review-frame").bounding_box()
    assert box["width"] > 1000                                           # full width
    # one scrollbar: cutter's page fits the frame (only its list of comments scrolls)
    page.wait_for_function("() => { const f = document.getElementById('review-frame'); const d = f.contentDocument; "
                           "return d && d.body.classList.contains('embedded') "
                           "&& d.documentElement.scrollHeight <= f.clientHeight + 1; }")
    frame = page.frame_locator("#review-frame")
    frame.locator("#note").wait_for()
    assert frame.locator("#name-button").is_hidden() or frame.locator("#name-button").is_disabled()   # the login is the name
    page.evaluate("() => { const v = document.getElementById('review-frame').contentDocument.getElementById('video'); "
                  "v.currentTime = 13.0; }")
    page.wait_for_timeout(500)
    frame.locator("#note").fill("Shorter here")
    frame.locator("#note").press("Enter")
    api = f"/projects/{pid}/review/api/entries?cut=cut1"
    page.wait_for_function(f"() => fetch('{api}').then(r => r.json()).then(d => d.entries.length === 1)")
    e = page.evaluate(f"() => fetch('{api}').then(r => r.json()).then(d => d.entries[0])")
    assert e["section"] == "Glacier" and abs(e["t"] - 13.0) < 0.5 and e["person"] == "cutlery"
    page.wait_for_function("() => document.getElementById('review-state').textContent.includes('1')")
    page.click("#review-done")
    page.fill("#done-note", "Shorter overall.")
    page.click("#done-ok")
    page.wait_for_url(f"**/projects/{pid}")
    jobs = page.evaluate(f"() => fetch('/api/projects/{pid}/jobs').then(r => r.json())")
    assert jobs["jobs"][0]["cut"] == "cut2" and jobs["jobs"][0]["note"] == "Shorter overall."
    assert page.locator("#job-note-box").is_visible()
    page.goto(f"{server['url']}/projects/{pid}/review")
    page.wait_for_function("() => window.cutleryReview && window.cutleryReview.cuts.length")
    assert page.locator("#review-done").is_hidden()
    server["app"].state.jobs.cancel_project(pid)
