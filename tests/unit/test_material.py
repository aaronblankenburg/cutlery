"""The material page: the footage by day from the analysis, the transcript of one clip, the user's selection."""
import json

from test_app import TestProjects
from test_sidecar import variant

CLIPS = [
    {"id": "C001", "file": "2012-07-15 08-00-00 GOPR0001.MP4", "rel_path": "B/2012-07-15 08-00-00 GOPR0001.MP4",
     "created": "2012-07-15 08:00:00", "duration": 12.0, "width": 1920, "height": 1080, "rotation": 0},
    {"id": "C002", "file": "2012-07-14 18-30-00 DSC_0001.MOV", "rel_path": "A/2012-07-14 18-30-00 DSC_0001.MOV",
     "created": "2012-07-14 18:30:00", "duration": 8.0, "width": 1920, "height": 1080, "rotation": 90},
    {"id": "C003", "file": "IMG_1.MOV", "rel_path": "A/IMG_1.MOV", "duration": 4.0, "width": 1080, "height": 1920},
    {"id": "C004", "file": "2012-07-14 09-00-00 DSC_0002.MOV", "rel_path": "A/2012-07-14 09-00-00 DSC_0002.MOV",
     "created": "2012-07-14 09:00:00", "duration": 5.0, "width": 1920, "height": 1080},
    {"id": "C009", "file": "gone.mov", "rel_path": "A/gone.mov", "created": "2012-07-14 10:00:00", "duration": 3.0},
]


def project(client, cfg):
    pid = TestProjects().create(client, "Namibia")
    a = cfg.work_dir / "projects" / pid / "analysis"
    (a / "transcripts").mkdir(parents=True)
    (a / "image").mkdir()
    (a / "strips").mkdir()
    (a / "clips.json").write_text(json.dumps(CLIPS))
    for c in CLIPS[:4]:                                  # C009 is not one of Cutlery's clips: not shown
        r = client.post(f"/api/projects/{pid}/clips", json=variant(rel_path=c["rel_path"], file=c["file"],
                                                                   fingerprint__head_sha256=c["id"][-1] * 64))
        assert r.status_code == 200, r.text
    (a / "transcripts" / "C001.json").write_text(json.dumps({"language": "de", "segments": [
        {"start": 0.5, "end": 2.0, "text": "Guten Morgen Wüste", "avg_logprob": -0.3,
         "words": [[0.5, 0.9, " Guten", 0.9], [0.9, 1.4, " Morgen", 0.9], [1.4, 2.0, " Wüste", 0.8]]},
        {"start": 3.0, "end": 4.0, "text": "Vielen Dank fürs Zuschauen", "avg_logprob": -1.4, "words": []}]}))
    (a / "image" / "C001.json").write_text(json.dumps({"usable": [{"start": 0, "end": 10, "steady": True}],
                                                      "flaws": [{"start": 10, "end": 12, "why": ["shaky"]}],
                                                      "summary": {"speech_s": 3.5}}))
    (a / "strips" / "C001.jpg").write_bytes(b"\xff\xd8")
    (a / "people.json").write_text(json.dumps({"people": [{"id": "P1", "name": "Aaron", "seconds": 9,
                                                            "clips": {"C001": [{"start": 0, "end": 5}]}}]}))
    cuts = cfg.data_dir / "projects" / pid / "cuts"
    for cut, shots in (("cut1", [{"nr": 1, "clip": "C004", "in": 0, "out": 3}]),
                       ("cut2", [{"nr": 4, "clip": "C001", "in": 1, "out": 5,
                                  "cutaways": [{"clip": "C004", "in": 1, "out": 2, "at": 1}]}])):
        (cuts / cut).mkdir(parents=True)
        (cuts / cut / "edit.json").write_text(json.dumps({"sections": [{"name": "x", "shots": shots}]}))
    return pid


class TestFootage:
    def test_by_day_and_time_with_what_the_analysis_knows(self, client, cfg):
        pid = project(client, cfg)
        d = client.get(f"/api/projects/{pid}/material").json()
        assert [c["id"] for c in d["clips"]] == ["C004", "C002", "C001", "C003"]      # by recording time, no date last
        assert [(x["day"], x["clips"]) for x in d["days"]] == [("2012-07-14", 2), ("2012-07-15", 1), (None, 1)]
        c1 = next(c for c in d["clips"] if c["id"] == "C001")
        assert c1["words"] == 7 and c1["people"] == ["P1"] and c1["strip"] is True and c1["language"] == "de"
        assert c1["usable"] == [[0, 10, True]] and c1["flaws"] == [[10, 12, ["shaky"]]]
        assert c1["used"] == [{"cut": "cut2", "nr": 4, "in": 1, "out": 5, "cutaway": False}]
        assert c1["camera"] == "GOPR 1080p"
        c4 = next(c for c in d["clips"] if c["id"] == "C004")
        assert [u["cut"] for u in c4["used"]] == ["cut2", "cut1"] and c4["used"][0]["cutaway"] is True
        c2 = next(c for c in d["clips"] if c["id"] == "C002")
        assert (c2["width"], c2["height"]) == (1080, 1920)                            # turned upright
        assert d["cuts"] == ["cut1", "cut2"] and d["people"] == [{"id": "P1", "name": "Aaron", "seconds": 9}]

    def test_transcript_with_words_and_unsure_parts(self, client, cfg):
        pid = project(client, cfg)
        tr = client.get(f"/api/projects/{pid}/material/C001/transcript").json()
        assert tr["segments"][0]["words"][1] == [0.9, 1.4, " Morgen"] and not tr["segments"][0]["unsure"]
        assert tr["segments"][1]["unsure"] is True
        assert client.get(f"/api/projects/{pid}/material/x.y/transcript").json()["segments"] == []

    def test_pages_in_both_languages(self, client, cfg):
        pid = project(client, cfg)
        assert "Dein Footage nach Tagen" in client.get(f"/projects/{pid}/material", headers={"Accept-Language": "de"}).text
        page = client.get(f"/projects/{pid}/material", headers={"Accept-Language": "en"}).text
        assert "Your footage by day" in page and f'href="/projects/{pid}/material"' in page


class TestSelects:
    def test_favourites_and_rejects_go_into_selects_json_and_the_briefing(self, client, cfg):
        pid = project(client, cfg)
        client.put(f"/api/projects/{pid}/briefing", json={"prompt": "Eine Radreise."}, headers={"Accept-Language": "de"})
        url = f"/api/projects/{pid}/selects"
        r = client.post(url, json={"clip": "C001", "kind": "favorite", "in": 1, "out": 4.5, "note": " muss  rein "},
                        headers={"Accept-Language": "de"})
        assert r.status_code == 201, r.text
        fav = r.json()
        assert fav["id"] == "s1" and fav["note"] == "muss rein" and fav["file"] == CLIPS[0]["file"]
        rej = client.post(url, json={"clip": "C004", "kind": "reject"}, headers={"Accept-Language": "de"}).json()
        assert rej["in"] is None and rej["out"] is None
        stored = json.loads((cfg.data_dir / "projects" / pid / "selects.json").read_text())
        assert stored["version"] == 1 and [s["id"] for s in stored["selects"]] == ["s1", "s2"]
        md = (cfg.data_dir / "projects" / pid / "briefing.md").read_text()
        assert "## Meine Auswahl aus dem Footage" in md
        assert "C001 (2012-07-15 08-00-00 GOPR0001.MP4), 0:01.0–0:04.5 – muss rein" in md
        assert "C004 (2012-07-14 09-00-00 DSC_0002.MOV), ganzer Clip" in md
        assert client.get(f"/api/projects/{pid}/material").json()["selects"][0]["id"] == "s1"

        assert client.patch(f"{url}/s1", json={"note": "Sonnenaufgang"}).json()["note"] == "Sonnenaufgang"
        assert client.delete(f"{url}/s2", headers={"Accept-Language": "de"}).status_code == 200
        md = (cfg.data_dir / "projects" / pid / "briefing.md").read_text()
        assert "Sonnenaufgang" in md and "C004" not in md
        assert client.delete(f"{url}/s2").status_code == 404

    def test_refusals(self, client, cfg):
        pid = project(client, cfg)
        url = f"/api/projects/{pid}/selects"
        for body in ({"clip": "C009", "kind": "favorite"},                       # not one of the project's clips
                     {"clip": "C001", "kind": "maybe"},
                     {"clip": "C001", "kind": "favorite", "in": 5, "out": 2},
                     {"clip": "C001", "kind": "favorite", "in": 1, "out": 99},
                     {"clip": "C001", "kind": "favorite", "in": "x", "out": 2},
                     {"clip": "C001", "kind": "favorite", "note": "x" * 501}):
            assert client.post(url, json=body).status_code == 422, body
        assert client.patch(f"{url}/s9", json={"note": "x"}).status_code == 404
