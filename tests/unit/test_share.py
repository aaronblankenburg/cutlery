"""Sharing a project with a guest (share.py): a link per person, a cookie, a fixed list of what a guest may do,
suggestions in the material, notifications."""
import json

import pytest
from starlette.testclient import TestClient

from cutlery import share
from cutlery.notify import Notifier
from test_material import project


@pytest.fixture
def guest_link(client, cfg):
    pid = project(client, cfg)
    other = client.post("/projects/new", data={"name": "Privat"}, follow_redirects=False).headers["location"].split("/")[2]
    r = client.post(f"/api/projects/{pid}/shares", json={"name": "Alex", "days": 30})
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    assert url.startswith("http://testserver/s/") and "token" not in r.json()
    return pid, other, url[len("http://testserver"):]


def as_guest(app, link):
    c = TestClient(app, base_url="http://testserver")
    r = c.get(link, follow_redirects=False)
    assert r.status_code == 303 and share.COOKIE in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    return c, r.headers["location"]


class TestLink:
    def test_the_link_opens_the_review_and_only_what_a_guest_may(self, app, client, guest_link):
        pid, other, link = guest_link
        g, home = as_guest(app, link)
        assert home == f"/projects/{pid}/review"
        page = g.get(home, headers={"Accept-Language": "de"}).text
        assert "Mit dir geteilt, Alex" in page and "/settings" not in page and "/export" not in page
        assert g.get(f"/projects/{pid}/material").status_code == 200
        assert g.get(f"/api/projects/{pid}/material").json()["guest"] == "Alex"
        assert g.get(f"/projects/{pid}/review/api/cuts").status_code == 200
        # pages of the owner's: back to the review; everything else refused
        for path in ("/", "/settings", f"/projects/{pid}/setup", f"/projects/{pid}/export", f"/projects/{pid}/share",
                     f"/projects/{other}/review"):
            r = g.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == home, path
        for method, path, body in (
                ("GET", "/api/telegram", None), ("POST", "/api/telegram", {}), ("PUT", "/api/claude/token", {"token": "x"}),
                ("DELETE", f"/api/projects/{pid}/clips", None), ("PUT", f"/api/projects/{pid}/briefing", {"prompt": "x"}),
                ("POST", f"/api/projects/{pid}/jobs", {"kind": "draft"}), ("POST", f"/api/projects/{pid}/review/done", {"cut": "cut2"}),
                ("POST", f"/api/projects/{pid}/tags", {"action": "remove", "tag": "x"}),
                ("POST", f"/api/projects/{pid}/suggestions", {"action": "accept", "ids": ["g1"]}),
                ("POST", f"/api/projects/{pid}/shares", {"name": "Eve"}), ("GET", f"/api/projects/{pid}/shares", None),
                ("POST", f"/projects/{pid}/delete", None), ("POST", f"/projects/{pid}/review/api/status", {"ids": [1]}),
                ("GET", f"/api/projects/{other}/material", None), ("POST", "/files/", None)):
            r = g.request(method, path, json=body)
            assert r.status_code == 403, (method, path, r.status_code)

    def test_the_status_page_read_only(self, app, client, cfg, guest_link):
        pid, other, link = guest_link
        g, _ = as_guest(app, link)
        page = g.get(f"/projects/{pid}", headers={"Accept-Language": "de"})
        assert page.status_code == 200
        for owner_only in ('id="job-resume"', 'id="job-cancel"', 'id="job-raw"', 'id="memory-edit"', "data-person=",
                           'id="merge-mode"', f'/projects/{pid}/analysis"'):
            assert owner_only not in page.text, owner_only
        for path in (f"/api/projects/{pid}/jobs", f"/api/projects/{pid}/egress", f"/api/projects/{pid}/memory"):
            assert g.get(path).status_code == 200, path
        for method, path, body in (
                ("PUT", f"/api/projects/{pid}/people/P1", {"name": "Eve"}),
                ("POST", f"/api/projects/{pid}/people/merge", {"ids": ["P1", "P2"], "name": "Eve"}),
                ("GET", f"/api/projects/{pid}/people", None),
                ("POST", f"/api/projects/{pid}/egress", {"domain": "example.org", "allow": True}),
                ("PUT", f"/api/projects/{pid}/memory", {"text": "x"}),
                ("GET", f"/api/jobs/{'0' * 16}/events", None), ("GET", f"/api/jobs/{'0' * 16}/raw", None),
                ("POST", f"/api/jobs/{'0' * 16}/resume", None), ("POST", f"/api/jobs/{'0' * 16}/cancel", None),
                ("GET", f"/api/projects/{other}/jobs", None), ("GET", f"/projects/{other}", None)):
            r = g.request(method, path, json=body)
            assert r.status_code == 403, (method, path, r.status_code)

    def test_comments_are_the_guests(self, app, client, cfg):
        from test_pages import project_with_cut
        pid = project_with_cut(client, cfg, ("cut1", "cut2"))
        link = client.post(f"/api/projects/{pid}/shares", json={"name": "Alex"}).json()["url"][len("http://testserver"):]
        g, _ = as_guest(app, link)
        api = f"/projects/{pid}/review/api"
        e = g.post(f"{api}/entries", json={"cut": "cut2", "t": 1.0, "text": "Mehr Wüste", "person": "Aaron"})
        assert e.status_code in (200, 201), e.text
        entry = e.json()["entry"]
        assert entry["person"] == "Alex"                                   # whatever the request says
        mine = client.post(f"{api}/entries", json={"cut": "cut2", "t": 2.0, "text": "vom Owner"}).json()["entry"]
        assert g.patch(f"{api}/entries/{entry['id']}", json={"text": "Viel mehr Wüste"}).status_code == 200
        assert g.patch(f"{api}/entries/{mine['id']}", json={"text": "gekapert"}).status_code in (403, 409)
        assert g.delete(f"{api}/entries/{mine['id']}").status_code in (403, 409)

    def test_withdrawn_and_unknown_links(self, app, client, guest_link):
        pid, _, link = guest_link
        g, _ = as_guest(app, link)
        sid = client.get(f"/api/projects/{pid}/shares").json()["shares"][0]["id"]
        assert client.delete(f"/api/projects/{pid}/shares/{sid}").status_code == 200
        r = g.get(f"/projects/{pid}/review")
        assert r.status_code == 403 and "Max-Age=0" in r.headers["set-cookie"]
        anon = TestClient(app, base_url="http://testserver")
        assert anon.get(link, follow_redirects=False).status_code == 404
        assert anon.get("/s/abcdef012345.wrongwrongwrongwrongwrong", follow_redirects=False).status_code == 404
        s = client.get(f"/api/projects/{pid}/shares").json()["shares"][0]
        assert s["active"] is False and s["revoked"]
        # names: none, too long, the owner's login
        for name in ("", "x" * 41, "cutlery"):
            assert client.post(f"/api/projects/{pid}/shares", json={"name": name}).status_code == 422
        assert client.post(f"/api/projects/{pid}/shares", json={"name": "Bo", "days": 3}).status_code == 422

    def test_expired(self, app, client, guest_link):
        pid, _, link = guest_link
        db = app.state.db
        with db.tx() as con:
            con.execute("UPDATE shares SET expires='2020-01-01T00:00:00+00:00'")
        assert TestClient(app, base_url="http://testserver").get(link, follow_redirects=False).status_code == 404

    def test_the_owner_with_a_login_stays_the_owner(self, app, client, guest_link):
        pid, _, link = guest_link
        client.get(link, follow_redirects=False)              # the owner tries the link: Basic Auth wins
        assert client.get("/settings").status_code == 200


class TestSuggestions:
    def test_a_guests_marks_are_suggestions_the_owner_takes_over(self, app, client, cfg, guest_link):
        pid, _, link = guest_link
        g, _ = as_guest(app, link)
        client.post(f"/api/projects/{pid}/selects", json={"clip": "C002", "kind": "favorite"})
        r = g.post(f"/api/projects/{pid}/selects", json={"clip": "C001", "kind": "favorite", "in": 1, "out": 4, "note": "Sonnenaufgang"})
        assert r.status_code == 201 and r.json()["by"] == "Alex" and r.json()["id"].startswith("g")
        g.post(f"/api/projects/{pid}/selects", json={"items": [{"clip": "C001", "kind": "tag", "tag": "Grenze"},
                                                               {"clip": "C004", "kind": "tag", "tag": "Grenze"}]})
        data = cfg.data_dir / "projects" / pid
        assert [s["clip"] for s in json.loads((data / "selects.json").read_text())["selects"]] == ["C002"]
        assert len(json.loads((data / "suggestions.json").read_text())["selects"]) == 3
        # the guest may not change the owner's selection, only their own suggestions
        assert g.delete(f"/api/projects/{pid}/selects/s1").status_code == 404
        mat = g.get(f"/api/projects/{pid}/material").json()
        assert len(mat["suggestions"]) == 3 and [s["id"] for s in mat["selects"]] == ["s1"]
        # the owner sees them, takes two over, declines one
        sug = client.get(f"/api/projects/{pid}/material").json()["suggestions"]
        fav = next(s for s in sug if s["kind"] == "favorite")
        tags = [s["id"] for s in sug if s["kind"] == "tag"]
        r = client.post(f"/api/projects/{pid}/suggestions", json={"action": "accept", "ids": [fav["id"], tags[0]]}).json()
        assert r["done"] == 2 and len(r["suggestions"]) == 1
        taken = next(s for s in r["selects"] if s["clip"] == "C001" and s["kind"] == "favorite")
        assert taken["note"] == "Sonnenaufgang (Alex)" and "by" not in taken and taken["id"].startswith("s")
        assert client.post(f"/api/projects/{pid}/suggestions", json={"action": "decline", "ids": [tags[1]]}).json()["suggestions"] == []
        assert client.post(f"/api/projects/{pid}/suggestions", json={"action": "maybe", "ids": []}).status_code == 422


class TestNotify:
    def test_a_guests_activity_is_one_message(self, cfg, monkeypatch):
        sent = []

        class FakeTelegram:
            def send(self, key, buttons=None, **v):
                sent.append((key, v))
        n = Notifier(cfg, FakeTelegram())
        monkeypatch.setattr(Notifier, "GUEST_WAIT_S", 0.05)
        p = {"id": "abc", "name": "Namibia"}
        for cut in ("cut2", "cut2", "cut3"):
            n.guest_activity(p, "Alex", "comment", cut)
        n.guest_activity(p, "Alex", "suggestion")
        import time
        time.sleep(0.3)
        assert [k for k, _ in sent] == ["telegram.msg.guest_comments", "telegram.msg.guest_suggestions"]
        assert sent[0][1]["n"] == 3 and sent[0][1]["cuts"] == "2, 3" and sent[1][1]["n"] == 1


def test_the_list_of_paths():
    pid = "a" * 10
    assert share.guest_may("GET", f"/projects/{pid}/review", pid)
    assert share.guest_may("POST", f"/projects/{pid}/review/api/entries", pid)
    assert not share.guest_may("POST", f"/projects/{pid}/review/api/submit", pid)
    assert not share.guest_may("GET", f"/projects/{'b' * 10}/review", pid)
    assert not share.guest_may("DELETE", f"/api/projects/{pid}/clips/C1", pid)
    assert share.guest_may("GET", "/static/css/cutlery.css", pid) and not share.guest_may("POST", "/static/x", pid)
    assert share.guest_may("GET", f"/projects/{pid}", pid) and not share.guest_may("POST", f"/projects/{pid}", pid)
    assert share.guest_may("GET", f"/api/projects/{pid}/jobs/{'0' * 16}/events", pid)
    assert not share.guest_may("GET", f"/api/projects/{pid}/jobs/{'0' * 16}/raw", pid)
    assert not share.guest_may("POST", f"/api/projects/{pid}/jobs", pid)
