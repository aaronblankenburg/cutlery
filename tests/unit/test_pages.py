"""M5: the pages after docs/design and their APIs (review on cutter's API, files, people, readiness)."""
import json

import pytest

from conftest import auth_header
from cutlery import cutter_project
from test_app import TestProjects
from test_sidecar import GOOD, variant

TIMELINE = {"format": 2, "version": "cut1", "title": "Island (cut1)", "fps": 25, "duration": 30.0,
            "sections": [{"name": "Arrival", "start": 0.0, "end": 12.0}, {"name": "Glacier", "start": 12.0, "end": 30.0}],
            "shots": [{"nr": 1, "section": "Arrival", "clip": "C001", "start": 0.0, "end": 12.0, "source_in": 1.0},
                      {"nr": 2, "section": "Glacier", "clip": "C002", "start": 12.0, "end": 30.0, "source_in": 0.0}],
            "edit_points": [{"t": 12.0, "before": 1, "after": 2}], "music": [], "subtitles": [],
            "decisions": {"entries": [{"id": "len", "kind": "assumption", "title": "Length", "t": 5.0, "text": "About 5 min."}]},
            "video": {"file": "review.mp4"}}


def project_with_cut(client, cfg, cuts=("cut1",)):
    pid = TestProjects().create(client, "Island 2026")
    for cut in cuts:
        d = cfg.work_dir / "projects" / pid / "review" / "cuts" / cut
        d.mkdir(parents=True)
        (d / "timeline.json").write_text(json.dumps(dict(TIMELINE, version=cut)))
        (d / "review.mp4").write_bytes(b"\0" * 1000)
        (cfg.data_dir / "projects" / pid / "cuts" / cut).mkdir(parents=True)
        (cfg.data_dir / "projects" / pid / "cuts" / cut / "edit.json").write_text("{}")
    return pid


class TestReview:
    def test_comments_through_cutters_api(self, client, cfg):
        pid = project_with_cut(client, cfg)
        api = f"/projects/{pid}/review/api"
        cuts = client.get(f"{api}/cuts").json()
        assert [c["id"] for c in cuts["cuts"]] == ["cut1"] and cuts["cuts"][0]["locked"] is False
        r = client.post(f"{api}/entries", json={"cut": "cut1", "t": 13.5, "text": "Shorter here", "person": "someone else"})
        assert r.status_code == 201, r.text
        e = r.json()["entry"]
        assert (e["person"], e["section"], e["nr"]) == ("cutlery", "Glacier", 2)   # the login, enriched by cutter
        entries = client.get(f"{api}/entries?cut=cut1").json()["entries"]
        assert [x["text"] for x in entries] == ["Shorter here"]
        # comments are stored in the data tree, renders come from the work tree
        assert (cfg.data_dir / "projects" / pid / ".cutter/review/data/feedback.sqlite").is_file()
        assert client.delete(f"{api}/entries/{e['id']}").status_code == 200
        assert client.get(f"{api}/entries?cut=cut1").json()["entries"] == []

    def test_media_with_ranges_and_no_escape(self, client, cfg):
        pid = project_with_cut(client, cfg)
        r = client.get(f"/projects/{pid}/review/media/cut1/review.mp4", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100
        assert client.get(f"/projects/{pid}/review/media/cut1/timeline.json").json()["title"] == "Island (cut1)"
        for bad in ("../../x", "..", "cut9"):
            assert client.get(f"/projects/{pid}/review/media/{bad}/review.mp4").status_code == 404
        assert client.get(f"/projects/{pid}/review/media/cut1/feedback.sqlite").status_code == 404

    def test_review_done_locks_and_queues_the_next_cut(self, client, cfg, app, monkeypatch):
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")
        pid = project_with_cut(client, cfg)
        api = f"/projects/{pid}/review/api"
        e = client.post(f"{api}/entries", json={"cut": "cut1", "t": 2.0, "text": "x"}).json()["entry"]
        r = client.post(f"/api/projects/{pid}/review/done", json={"cut": "cut1"})
        assert r.status_code == 201 and r.json()["cut"] == "cut2"
        assert client.get(f"{api}/cuts").json()["cuts"][0]["locked"] is True
        # a locked cut takes no more comments, changes or deletions
        assert client.post(f"{api}/entries", json={"cut": "cut1", "t": 3.0, "text": "late"}).status_code == 409
        assert client.patch(f"{api}/entries/{e['id']}", json={"text": "changed"}).status_code == 409
        assert client.delete(f"{api}/entries/{e['id']}").status_code == 409
        # a second click does not queue a second job
        assert client.post(f"/api/projects/{pid}/review/done", json={"cut": "cut1"}).status_code == 409
        app.state.jobs.cancel_project(pid)

    def test_only_the_newest_cut_can_be_finished(self, client, cfg):
        pid = project_with_cut(client, cfg, ("cut1", "cut2"))
        r = client.post(f"/api/projects/{pid}/review/done", json={"cut": "cut1"}, headers={"Accept-Language": "de"})
        assert r.status_code == 409 and "neueste" in r.json()["message"]

    def test_review_page(self, client, cfg):
        pid = project_with_cut(client, cfg)
        page = client.get(f"/projects/{pid}/review", headers={"Accept-Language": "de"}).text
        assert "Vorschau aus den 360p-Kopien" in page and "Fertig → Export" in page and "review.js" in page
        empty = TestProjects().create(client, "Empty")
        assert "noch keine Fassung" in client.get(f"/projects/{empty}/review", headers={"Accept-Language": "de"}).text


class TestFiles:
    def test_upload_licence_and_briefing(self, client, cfg):
        pid = TestProjects().create(client, "Island")
        client.put(f"/api/projects/{pid}/briefing", json={"prompt": "A trip."}, headers={"Accept-Language": "de"})
        r = client.put(f"/api/projects/{pid}/files/music/nordlicht%20demo.wav", content=b"RIFF0000WAVE")
        assert r.status_code == 200 and r.json()["music"][0]["name"] == "nordlicht demo.wav"
        assert (cfg.data_dir / "projects" / pid / "music" / "nordlicht demo.wav").read_bytes() == b"RIFF0000WAVE"
        r = client.put(f"/api/projects/{pid}/music/nordlicht%20demo.wav/license", json={"license": "own"},
                       headers={"Accept-Language": "de"})
        assert r.json()["music"][0]["license"] == "own"
        client.put(f"/api/projects/{pid}/files/music/road.mp3", content=b"ID3", headers={"Accept-Language": "de"})
        client.put(f"/api/projects/{pid}/files/storyboard/plan.pdf", content=b"%PDF", headers={"Accept-Language": "de"})
        client.put(f"/api/projects/{pid}/files/other/route.gpx", content=b"<gpx/>", headers={"Accept-Language": "de"})
        md = (cfg.data_dir / "projects" / pid / "briefing.md").read_text()
        assert "music/nordlicht demo.wav: Eigene Musik – verwendbar" in md
        assert "music/road.mp3: Andere / unklar – nicht verwenden" in md and "storyboard/plan.pdf" in md
        state = client.get(f"/api/projects/{pid}/state").json()
        assert state["music_unclear"] == 1 and len(state["files"]["storyboard"]) == 1
        assert "other/route.gpx" in md and state["files"]["other"][0]["name"] == "route.gpx"     # any file type
        assert client.delete(f"/api/projects/{pid}/files/music/road.mp3").json()["music"][0]["name"] == "nordlicht demo.wav"

    @pytest.mark.parametrize("kind, name", [("music", "x.exe"), ("images", "../x.png"), ("secrets", "a.png")])
    def test_refused(self, client, kind, name):
        pid = TestProjects().create(client)
        r = client.put(f"/api/projects/{pid}/files/{kind}/{name}", content=b"x")
        assert r.status_code in (404, 422)

    def test_names_are_cleaned(self, client, cfg):
        pid = TestProjects().create(client)
        r = client.put(f"/api/projects/{pid}/files/storyboard/.hidden.pdf", content=b"%PDF")
        assert [f["name"] for f in r.json()["storyboard"]] == ["hidden.pdf"]

    def test_licence_must_be_known(self, client):
        pid = TestProjects().create(client)
        client.put(f"/api/projects/{pid}/files/music/a.mp3", content=b"x")
        assert client.put(f"/api/projects/{pid}/music/a.mp3/license", json={"license": "pirated"}).status_code == 422


class TestPeople:
    def test_names_without_face_vectors(self, client, cfg):
        pid = TestProjects().create(client)
        a = cfg.work_dir / "projects" / pid / "analysis"
        a.mkdir(parents=True)
        (a / "people.json").write_text(json.dumps({"people": [
            {"id": "P1", "name": "", "seconds": 41, "clips": {"C001": []}, "pictures": ["faces/C001_0.jpg"],
             "vectors": [[0.1, 0.2]]}]}))
        people = client.get(f"/api/projects/{pid}/people").json()
        assert people == [{"id": "P1", "name": "", "seconds": 41, "clips": ["C001"], "pictures": ["C001_0.jpg"]}]
        r = client.put(f"/api/projects/{pid}/people/P1", json={"name": "Anna"})
        assert r.status_code == 200 and r.json()[0]["name"] == "Anna"
        assert client.put(f"/api/projects/{pid}/people/X1", json={"name": "x"}).status_code == 404


class TestPages:
    def test_all_pages_in_both_languages(self, client, cfg):
        pid = project_with_cut(client, cfg)
        a = cfg.work_dir / "projects" / pid / "analysis"
        a.mkdir(parents=True)
        (a / "clips.json").write_text(json.dumps([{"id": "C001", "file": "a.mov", "rel_path": "Tag 1/a.mov", "duration": 3}]))
        client.post(f"/api/projects/{pid}/clips", json=variant(rel_path="Tag 1/a.mov", file="a.mov"))   # shown: known clip
        (a / "transcripts").mkdir()
        (a / "transcripts" / "C001.json").write_text(json.dumps({"segments": [{"text": "Wir waren zu spät."}]}))
        for lang, words in (("de", ["Bereit für den Entwurf?", "Fassungen", "Contact Sheets und Transkripte",
                                    "Sichtung abschließen", "Projekte"]),
                            ("en", ["Ready for the draft?", "Cuts", "Contact sheets and transcripts",
                                    "Review done", "Projects"])):
            h = {"Accept-Language": lang}
            text = "".join(client.get(u, headers=h).text for u in
                           (f"/projects/{pid}/setup", f"/projects/{pid}", f"/projects/{pid}/analysis",
                            f"/projects/{pid}/review", "/"))
            for w in words[:3] + words[4:]:
                assert w in text, (lang, w)
        assert "Wir waren zu spät." in client.get(f"/projects/{pid}/analysis").text
        assert client.get(f"/projects/{pid}/footage", follow_redirects=False).headers["location"] == f"/projects/{pid}/setup"

    def test_analysis_player_serves_the_360p_copy_with_ranges(self, client, cfg):
        pid = TestProjects().create(client)
        a = cfg.work_dir / "projects" / pid / "analysis"
        a.mkdir(parents=True)
        (a / "clips.json").write_text(json.dumps([{"id": "C001", "rel_path": "Tag 1/a.mov"},
                                                  {"id": "C002", "rel_path": "../../../etc/passwd"}]))
        client.post(f"/api/projects/{pid}/clips", json=variant(rel_path="Tag 1/a.mov", file="a.mov"))
        standin = cfg.work_dir / "projects" / pid / "media" / "Tag 1" / "a.mov.mp4"
        standin.parent.mkdir(parents=True)
        standin.write_bytes(b"0123456789")
        r = client.get(f"/projects/{pid}/analysis/clip/C001.mp4", headers={"Range": "bytes=2-5"})
        assert r.status_code == 206 and r.content == b"2345" and r.headers["content-type"] == "video/mp4"
        assert client.get(f"/projects/{pid}/analysis/clip/C002.mp4").status_code == 404     # outside the media folder
        assert client.get(f"/projects/{pid}/analysis/clip/C009.mp4").status_code == 404
        page = client.get(f"/projects/{pid}/analysis").text
        assert f"/projects/{pid}/analysis/clip/C001.mp4" in page and 'id="lightbox"' in page

    def test_error_page_is_html_for_browsers(self, client):
        r = client.get("/projects/nope", headers={"Accept": "text/html", "Accept-Language": "de"})
        assert r.status_code == 404 and "Nicht gefunden" in r.text and "<html" in r.text
        assert client.get("/api/projects/nope/state").json() == {"error": "unknown project"}

    def test_readiness(self, client, app, cfg):
        pid = TestProjects().create(client)
        st = client.get(f"/api/projects/{pid}/state").json()
        assert st["ready"] == "upload" and st["usable"] == 0
        cid = client.post(f"/api/projects/{pid}/clips", json=GOOD).json()["id"]
        app.state.projects.mark(pid, cid, "uploaded")
        assert client.get(f"/api/projects/{pid}/state").json()["ready"] == "analysis"
        a = cfg.work_dir / "projects" / pid / "analysis"
        a.mkdir(parents=True)
        (a / "clips.json").write_text(json.dumps([{"id": "C001", "original": {"fingerprint": GOOD["fingerprint"]}}]))
        assert client.get(f"/api/projects/{pid}/state").json()["ready"] is None


def test_settings_page_and_token_api_without_runner(client):
    for lang, words in (("de", "Claude-Zugang"), ("en", "Claude access")):
        r = client.get("/settings", headers={"Accept-Language": lang})
        assert r.status_code == 200 and words in r.text
    r = client.put("/api/claude/token", json={"token": "sk-ant-oat01-" + "a" * 40})
    assert r.status_code == 409                                      # only the runner stores tokens
