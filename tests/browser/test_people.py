"""Browser test: people are merged by ticking them, or by giving one the name of another."""
import json

import pytest

from test_upload import new_project

pytestmark = pytest.mark.browser


def people_file(server, pid, people):
    a = server["cfg"].work_dir / "projects" / pid / "analysis"
    a.mkdir(parents=True, exist_ok=True)
    (a / "people.json").write_text(json.dumps({"people": people}))
    return a / "people.json"


def test_merge_by_ticking_and_by_name(page, server):
    pid = new_project(page, server)
    f = people_file(server, pid, [
        {"id": "P1", "name": "", "seconds": 10, "clips": {"C001": []}, "pictures": []},
        {"id": "P2", "name": "Anna", "seconds": 30, "clips": {"C002": []}, "pictures": []},
        {"id": "P3", "name": "Ben", "seconds": 5, "clips": {"C003": []}, "pictures": []},
        {"id": "P4", "name": "", "seconds": 2, "clips": {"C004": []}, "pictures": []}])
    page.goto(f"{server['url']}/projects/{pid}")
    assert page.is_hidden("#merge-bar") and page.is_hidden("#pick-P1")        # no boxes until asked for

    # "Merge people": boxes and the bar; two faces enable "Merge these 2"; the dialog keeps the one seen longest
    page.click("#merge-mode")
    assert page.is_visible("#merge-bar") and page.is_visible("#pick-P1")
    assert page.is_disabled("#merge-open")
    page.click("[data-row=P1] .face")
    assert page.is_disabled("#merge-open")
    page.click("[data-row=P2] .face")
    assert page.is_enabled("#merge-open")
    page.click("#merge-open")
    page.wait_for_selector("#merge-dialog[open]")
    assert page.input_value("#merge-name") == "Anna"
    assert page.locator("#merge-faces .merge-face").count() == 2
    page.click("#merge-ok")
    page.wait_for_selector("#people-status:has-text('P2')")
    ids = [p["id"] for p in json.loads(f.read_text())["people"]]
    assert ids == ["P2", "P3", "P4"]

    # the name of another person: asked whether they are the same; cancelling keeps the field as it was
    page.fill("#person-P4", "ben")
    page.press("#person-P4", "Tab")
    page.wait_for_selector("#merge-dialog[open]")
    page.click("#merge-dialog button[value=cancel]")
    page.wait_for_function("() => document.getElementById('person-P4').value === ''")
    page.fill("#person-P4", "Ben")
    page.press("#person-P4", "Tab")
    page.wait_for_selector("#merge-dialog[open]")
    page.click("#merge-ok")
    page.wait_for_selector("#person-P4", state="detached")
    page.wait_for_selector("#people-status:has-text('P3')")          # said after the reload
    data = json.loads(f.read_text())["people"]
    assert [(p["id"], p["name"]) for p in data] == [("P2", "Anna"), ("P3", "Ben")]
