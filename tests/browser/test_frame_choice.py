"""Browser test: a new project as a reel (9:16) – the format cards, the briefing select keeps it."""
import os

import pytest

pytestmark = pytest.mark.browser


def test_reel_project(page, server):
    page.goto(server["url"] + "/projects/new")
    page.fill("#name", "Reels")
    assert page.is_checked("input[name=frame][value='16:9']")
    page.click("label.frame-option:has(input[value='9:16'])")
    assert page.is_checked("input[name=frame][value='9:16']")
    if os.environ.get("CUTLERY_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["CUTLERY_SCREENSHOTS"], "new_project_frame.png"), full_page=True)
    page.click("button[type=submit]")
    page.wait_for_url("**/setup")
    assert page.eval_on_selector("#b-frame", "s => s.value") == "9:16"
    page.fill("#b-prompt", "Ein Reel")
    page.select_option("#b-frame", "4:5")
    page.click("#briefing button[type=submit]")
    page.wait_for_function("() => document.getElementById('briefing-status').textContent.length > 0")
    page.reload()
    assert page.eval_on_selector("#b-frame", "s => s.value") == "4:5"
