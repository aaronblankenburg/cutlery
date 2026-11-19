"""The picture format of a project: a film (16:9), a reel (9:16), a feed post … chosen when it is created, changed in
the briefing; it goes into cutter.toml and briefing.md, cuts made before a change keep theirs."""
import json

from cutlery import cutter_project
from cutlery.storage import ProjectPaths


def create(client, frame=None):
    data = {"name": "Reels Namibia"}
    if frame:
        data["frame"] = frame
    r = client.post("/projects/new", data=data, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].split("/")[2]


def test_new_project_page_offers_the_formats(client):
    page = client.get("/projects/new", headers={"Accept-Language": "de"}).text
    for f in ("16:9", "9:16", "4:5", "1:1", "21:9"):
        assert f'value="{f}"' in page
    assert "Instagram Reels" in page


def test_a_reel_project_writes_its_frame_to_cutter_toml_and_the_briefing(client, cfg):
    pid = create(client, "9:16")
    paths = ProjectPaths(cfg, pid)
    assert cutter_project.frame(paths) == "9:16"
    toml = cutter_project.render(paths, "Reels Namibia")
    assert '[timeline]\nframe = "9:16"' in toml
    r = client.put(f"/api/projects/{pid}/briefing", json={"prompt": "Drei Reels von der Radreise."},
                   headers={"Accept-Language": "de"})
    assert r.status_code == 200, r.text
    md = (cfg.data_dir / "projects" / pid / "briefing.md").read_text()
    assert "## Format" in md and "9:16 – Reel, hochkant" in md
    assert client.get(f"/api/projects/{pid}/briefing").json()["frame"] == "9:16"
    assert "9:16" in client.get("/").text


def test_default_is_a_film_and_unknown_formats_are_refused(client, cfg):
    pid = create(client)
    assert cutter_project.frame(ProjectPaths(cfg, pid)) == "16:9"
    r = client.post("/projects/new", data={"name": "X", "frame": "5:7"}, follow_redirects=False)
    assert r.status_code == 200 and "notice error" in r.text
    r = client.put(f"/api/projects/{pid}/briefing", json={"prompt": "x", "frame": "5:7"})
    assert r.status_code == 422


def test_changing_the_format_keeps_the_cuts_made_before(client, cfg):
    pid = create(client)
    paths = ProjectPaths(cfg, pid)
    for cut, edit in (("cut1", {"title": "A"}), ("cut2", {"title": "B", "frame": "1:1"})):
        (paths.data / "cuts" / cut).mkdir(parents=True)
        (paths.data / "cuts" / cut / "edit.json").write_text(json.dumps(edit))
    r = client.put(f"/api/projects/{pid}/briefing", json={"prompt": "Jetzt als Reels.", "frame": "9:16"})
    assert r.status_code == 200
    assert cutter_project.frame(paths) == "9:16"
    assert json.loads((paths.data / "cuts" / "cut1" / "edit.json").read_text())["frame"] == "16:9"
    assert json.loads((paths.data / "cuts" / "cut2" / "edit.json").read_text())["frame"] == "1:1"
    assert 'frame = "9:16"' in cutter_project.render(paths, "x")
