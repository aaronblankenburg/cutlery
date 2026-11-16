"""Browser test of the review page: comment at a moment, see the agent's notes, finish the review."""
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
    page.wait_for_function("() => window.cutleryReview && window.cutleryReview.cut")
    assert page.locator(".comment.decision.assumption").count() == 1          # the agent's assumption
    page.evaluate("() => { const v = document.getElementById('video'); v.currentTime = 13.0; }")
    page.wait_for_timeout(500)
    page.fill("#new", "Shorter here")
    page.click("#new-comment button[type=submit]")
    page.wait_for_function("() => window.cutleryReview.entries.length === 1")
    e = page.evaluate("() => window.cutleryReview.entries[0]")
    assert e["section"] == "Glacier" and abs(e["t"] - 13.0) < 0.5
    assert page.locator("#marks .mark.open").count() == 1
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("#review-done")
    page.wait_for_url(f"**/projects/{pid}")
    jobs = page.evaluate(f"() => fetch('/api/projects/{pid}/jobs').then(r => r.json())")
    assert jobs["jobs"][0]["cut"] == "cut2"
    page.goto(f"{server['url']}/projects/{pid}/review")
    page.wait_for_function("() => window.cutleryReview && window.cutleryReview.cut")
    assert page.locator("#review-locked").is_visible() and page.locator("#new").is_disabled()
    server["app"].state.jobs.cancel_project(pid)
