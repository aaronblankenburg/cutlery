"""Tags on the material page (what the analysis found, the user's own in selects.json, several clips at once) and the
parts of the analysis the user chooses."""
import json

from cutlery import cutter_project
from cutlery.storage import ProjectPaths
from test_material import project


def per_second(n, tags=None, objects=None):
    out = []
    for i in range(n):
        p = {"t": i}
        if tags:
            p["tags"] = [[tags(i), 0.8], ["sky", 0.1]]
        if objects and objects(i):
            p["objects"] = {objects(i): 1}
        out.append(p)
    return out


def with_analysis(client, cfg):
    pid = project(client, cfg)
    img = cfg.work_dir / "projects" / pid / "analysis" / "image"
    (img / "C001.json").write_text(json.dumps({
        "duration": 12.0, "tags_key": "abc", "usable": [], "flaws": [],
        "per_second": per_second(12, tags=lambda i: "desert" if i < 8 else "road",
                                 objects=lambda i: "dog" if 2 <= i < 6 else None),
        "camera": [{"start": 0, "end": 3.5, "move": "pan_left"}, {"start": 3.5, "end": 12, "move": "shaky"}]}))
    (img / "C004.json").write_text(json.dumps({"duration": 5.0, "per_second": per_second(5, tags=lambda i: "sea")}))
    return pid


class TestFound:
    def test_tiles_get_what_is_in_the_picture_objects_and_moves(self, client, cfg):
        pid = with_analysis(client, cfg)
        clips = {c["id"]: c for c in client.get(f"/api/projects/{pid}/material").json()["clips"]}
        c1 = clips["C001"]
        assert c1["tags"] == [["desert", 8.0], ["road", 4.0]]
        assert c1["objects"] == [["dog", 4.0]]
        assert c1["moves"] == [["pan_left", 3.5]]                     # "shaky" is a flaw, not a tag
        assert clips["C004"]["tags"] == []                            # no tags_key: tags from an older model
        assert clips["C003"]["tags"] == [] and clips["C003"]["objects"] == []

    def test_the_monitor_gets_the_ranges(self, client, cfg):
        pid = with_analysis(client, cfg)
        d = client.get(f"/api/projects/{pid}/material/C001/tags").json()
        assert d["tags"] == {"desert": [[0, 8]], "road": [[8, 12.0]]}
        assert d["objects"] == {"dog": [[2, 6]]} and d["moves"] == {"pan_left": [[0, 3.5]]}
        assert client.get(f"/api/projects/{pid}/material/../tags").status_code in (404, 405)
        assert client.get(f"/api/projects/{pid}/material/C003/tags").json() == {"tags": {}, "objects": {}, "moves": {}}


class TestOwnTags:
    def test_a_tag_for_a_range_and_one_for_several_clips(self, client, cfg):
        pid = with_analysis(client, cfg)
        r = client.post(f"/api/projects/{pid}/selects", json={"clip": "C001", "kind": "tag", "tag": "  Grenz  übergang ",
                                                             "in": 2, "out": 6})
        assert r.status_code == 201, r.text
        assert r.json()["tag"] == "Grenz übergang" and r.json()["in"] == 2
        items = [{"clip": c, "kind": "tag", "tag": "Camp"} for c in ("C001", "C002", "C004")]
        r = client.post(f"/api/projects/{pid}/selects", json={"items": items})
        assert r.status_code == 201 and len(r.json()["selects"]) == 3
        again = client.post(f"/api/projects/{pid}/selects", json={"items": items[:1]}).json()["selects"]
        assert again[0]["id"] == r.json()["selects"][0]["id"]          # the same tag twice: kept once
        sel = client.get(f"/api/projects/{pid}/selects").json()["selects"]
        assert sum(1 for s in sel if s.get("tag") == "Camp") == 3
        # briefing.md lists them by tag, for Claude
        client.put(f"/api/projects/{pid}/briefing", json={"prompt": "Ein Film."}, headers={"Accept-Language": "de"})
        md = (cfg.data_dir / "projects" / pid / "briefing.md").read_text()
        assert "**Meine Tags**" in md and "- Camp: C001" in md and "- Grenz übergang: C001" in md
        # renamed and removed like the other marks
        sid = sel[0]["id"]
        assert client.patch(f"/api/projects/{pid}/selects/{sid}", json={"tag": "Grenze"}).json()["tag"] == "Grenze"
        assert client.patch(f"/api/projects/{pid}/selects/{sid}", json={"kind": "favorite"}).json().get("tag") is None

    def test_bad_tags_are_refused(self, client, cfg):
        pid = with_analysis(client, cfg)
        for body in ({"clip": "C001", "kind": "tag"}, {"clip": "C001", "kind": "tag", "tag": "x" * 41},
                     {"items": []}, {"items": [{"clip": "C001", "kind": "tag", "tag": "ok"}, {"clip": "nope", "kind": "tag", "tag": "a"}]}):
            assert client.post(f"/api/projects/{pid}/selects", json=body).status_code == 422, body
        assert client.get(f"/api/projects/{pid}/selects").json()["selects"] == []      # all or none

    def test_page_has_the_tag_controls(self, client, cfg):
        pid = with_analysis(client, cfg)
        page = client.get(f"/projects/{pid}/material", headers={"Accept-Language": "de"}).text
        assert 'id="m-tags-btn"' in page and 'id="mon-tag-form"' in page and "Tag vergeben" in page


class TestParts:
    def test_chosen_when_the_project_is_made(self, client, cfg):
        page = client.get("/projects/new", headers={"Accept-Language": "de"}).text
        assert "Was die Analyse auswertet" in page and 'name="part_tags"' in page
        r = client.post("/projects/new", data={"name": "Ohne Text", "parts_sent": "1", "part_faces": "1",
                                               "part_tags": "1"}, follow_redirects=False)
        pid = r.headers["location"].split("/")[2]
        paths = ProjectPaths(cfg, pid)
        assert cutter_project.analysis_parts(paths) == ["faces", "tags"]
        assert '[analysis]\nparts = ["faces", "tags"]' in cutter_project.render(paths, "x")
        r = client.post("/projects/new", data={"name": "Alles"}, follow_redirects=False)
        assert cutter_project.analysis_parts(ProjectPaths(cfg, r.headers["location"].split("/")[2])) == list(
            cutter_project.ANALYSIS_PARTS)

    def test_changed_later_analyses_again(self, client, cfg, monkeypatch):
        pid = project(client, cfg)
        asked = []
        monkeypatch.setattr(client.app.state.worker, "request", asked.append)
        assert client.get(f"/api/projects/{pid}/analysis/parts").json()["parts"] == list(cutter_project.ANALYSIS_PARTS)
        r = client.put(f"/api/projects/{pid}/analysis/parts", json={"parts": ["transcripts", "tags"]})
        assert r.json() == {"parts": ["transcripts", "tags"], "changed": True} and asked == [pid]
        toml = (cfg.data_dir / "projects" / pid / "cutter.toml").read_text()
        assert 'parts = ["transcripts", "tags"]' in toml
        assert client.put(f"/api/projects/{pid}/analysis/parts", json={"parts": ["tags", "transcripts"]}).json()["changed"] is False
        assert asked == [pid]
        assert client.put(f"/api/projects/{pid}/analysis/parts", json={"parts": ["smell"]}).status_code == 422
        assert client.put(f"/api/projects/{pid}/analysis/parts", json={"parts": "tags"}).status_code == 422
        assert "Was die Analyse auswertet" in client.get(f"/projects/{pid}/setup", headers={"Accept-Language": "de"}).text


class TestPoster:
    def test_a_sharp_still_from_the_stand_in(self, client, cfg):
        import shutil
        import subprocess
        import pytest
        if not shutil.which("ffmpeg"):
            pytest.skip("needs ffmpeg")
        pid = project(client, cfg)
        paths = ProjectPaths(cfg, pid)
        src = paths.standin("B/2012-07-15 08-00-00 GOPR0001.MP4")
        src.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:d=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
        r = client.get(f"/projects/{pid}/material/poster/C001.jpg")
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
        made = paths.work / "posters" / "C001.jpg"
        assert made.is_file()
        stamp = made.stat().st_mtime_ns
        assert client.get(f"/projects/{pid}/material/poster/C001.jpg").status_code == 200
        assert made.stat().st_mtime_ns == stamp                                  # made once
        assert client.get(f"/projects/{pid}/material/poster/C003.jpg").status_code == 404   # no stand-in
        assert client.get(f"/projects/{pid}/material/poster/..%2Fx.jpg").status_code == 404


class TestManaging:
    def test_own_tags_deleted_renamed_and_removed_from_chosen_clips(self, client, cfg):
        pid = with_analysis(client, cfg)
        items = [{"clip": c, "kind": "tag", "tag": t} for c in ("C001", "C002", "C004") for t in ("Camp", "Zelt")]
        client.post(f"/api/projects/{pid}/selects", json={"items": items})
        api = f"/api/projects/{pid}/tags"
        r = client.post(api, json={"action": "remove", "tag": "Camp", "clips": ["C001", "C002"]}).json()
        assert r["removed"] == 2 and sorted(s["clip"] for s in r["selects"] if s["tag"] == "Camp") == ["C004"]
        r = client.post(api, json={"action": "rename", "tag": "Zelt", "to": "Camp"}).json()
        assert r["renamed"] == 3
        camp = sorted(s["clip"] for s in r["selects"] if s["tag"] == "Camp")
        assert camp == ["C001", "C002", "C004"]                                   # merged, C004 once
        assert client.post(api, json={"action": "remove", "tag": "Camp"}).json()["removed"] == 3
        assert client.get(f"/api/projects/{pid}/selects").json()["selects"] == []
        assert client.post(api, json={"action": "rename", "tag": "x", "to": ""}).status_code == 422
        assert client.post(api, json={"action": "explode"}).status_code == 422

    def test_wrong_automatic_tags_are_hidden(self, client, cfg):
        pid = with_analysis(client, cfg)
        api = f"/api/projects/{pid}/tags"
        assert client.post(api, json={"action": "hide", "key": "o:dog", "clip": "C001"}).status_code == 200
        r = client.post(api, json={"action": "hide", "key": "c:road"}).json()
        assert r["hidden"] == {"all": ["c:road"], "clips": {"C001": ["o:dog"]}}
        c1 = next(c for c in client.get(f"/api/projects/{pid}/material").json()["clips"] if c["id"] == "C001")
        assert c1["tags"] == [["desert", 8.0]] and c1["objects"] == []
        d = client.get(f"/api/projects/{pid}/material/C001/tags").json()
        assert "road" not in d["tags"] and d["objects"] == {}
        client.put(f"/api/projects/{pid}/briefing", json={"prompt": "Ein Film."}, headers={"Accept-Language": "de"})
        md = (cfg.data_dir / "projects" / pid / "briefing.md").read_text()
        assert "Falsch erkannte Tags der Analyse" in md and "Straße (überall)" in md and "Hund (C001)" in md
        r = client.post(api, json={"action": "hide", "key": "c:road", "hidden": False}).json()
        assert r["hidden"]["all"] == []
        assert client.post(api, json={"action": "hide", "key": "x:road"}).status_code == 422
        assert client.post(api, json={"action": "hide", "key": "c:road", "clip": "../x"}).status_code == 422
