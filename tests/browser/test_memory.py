"""Browser test: the project memory (CLAUDE.md) is edited in a large dialog; unsaved changes are asked about."""
import os

import pytest

from test_upload import new_project

pytestmark = pytest.mark.browser


def test_memory_editor(page, server):
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough")
    pid = new_project(page, server)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server['url']}/projects/{pid}")
    page.click("#memory-edit")
    page.wait_for_selector("#memory-dialog[open]")
    box = page.locator("#memory-input").bounding_box()
    assert box["width"] > 900 and box["height"] > 500                # large, not the narrow card
    if os.environ.get("SHOTS"):
        page.screenshot(path=os.path.join(os.environ["SHOTS"], "memory.png"))
    page.fill("#memory-input", "# Memory\n\n- Anna does not want to be in the film.")
    page.once("dialog", lambda d: d.dismiss())                      # "Discard?" no: stays open
    page.click("#memory-cancel")
    assert page.is_visible("#memory-dialog")
    page.click("#memory-dialog button[type=submit]")
    page.wait_for_selector("#memory-dialog", state="hidden")
    text = page.evaluate(f"() => fetch('/api/projects/{pid}/memory').then(r => r.json()).then(d => d.text)")
    assert "Anna does not want" in text
