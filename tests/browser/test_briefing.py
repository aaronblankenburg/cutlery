"""Browser test: the briefing form keeps what was entered."""
import pytest

from test_upload import new_project

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("lang", ["de", "en"])
def test_briefing_fields_are_kept(page, server, lang):
    page.context.add_cookies([{"name": "cutlery_lang", "value": lang, "url": server["url"]}])
    pid = new_project(page, server)
    page.fill("#b-prompt", "A bike tour.")
    page.fill("#b-purpose", "Friends")
    page.select_option("#b-length", index=3)
    chosen = page.input_value("#b-length")
    assert chosen
    page.fill("#b-style", "lively")
    page.locator("#briefing button[type=submit]").click()
    page.wait_for_function("() => document.getElementById('briefing-status').textContent.trim() !== ''")
    stored = page.evaluate(f"() => fetch('/api/projects/{pid}/briefing').then(r => r.json())")
    assert stored["length"] == chosen, stored
    page.reload()
    assert page.input_value("#b-length") == chosen
    assert page.input_value("#b-purpose") == "Friends"
