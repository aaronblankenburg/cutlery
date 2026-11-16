import io
import json
import zipfile

import pytest

from cutlery import export
from test_app import TestProjects
from test_sidecar import GOOD


def make_cut(cfg, pid, cut="cut1"):
    d = cfg.data_dir / "projects" / pid / "cuts" / cut
    d.mkdir(parents=True, exist_ok=True)
    (d / "edit.json").write_text("{}")


class TestRequest:
    def test_defaults_to_the_newest_cut(self):
        r = export.check_request({"os": "windows", "root": "D:\\x"}, ["cut1", "cut2"])
        assert (r["cut"], r["format"], r["relink"]) == ("cut2", "premiere", {})

    @pytest.mark.parametrize("form, key", [
        ({"os": "amiga", "root": "/x"}, "export.error.os"),
        ({"os": "macos", "root": " "}, "export.error.root"),
        ({"os": "macos", "root": "/x", "format": "fcp"}, "export.error.format"),
        ({"os": "macos", "root": "/x", "cut": "cut9"}, "export.error.cut"),
        ({"os": "macos", "root": "/x", "relink": "[1]"}, "export.error.failed"),
    ])
    def test_refusals(self, form, key):
        with pytest.raises(export.ExportError) as e:
            export.check_request(form, ["cut1"])
        assert e.value.key == key


class TestDownload:
    def test_page_without_a_cut(self, client):
        pid = TestProjects().create(client)
        assert "There is no cut yet" in client.get(f"/projects/{pid}/export", headers={"Accept-Language": "en"}).text

    def test_zip_with_localized_readme_and_licences(self, client, cfg):
        pid = TestProjects().create(client, "Island 2026")
        make_cut(cfg, pid, "cut1")
        make_cut(cfg, pid, "cut2")
        page = client.get(f"/projects/{pid}/export", headers={"Accept-Language": "de"}).text
        assert "Originale prüfen" in page and 'value="cut2" selected' in page
        relink = {"Tag 3/IMG_4821.MOV": "Tag 3/IMG_4821 Gletscher.MOV"}
        r = client.post(f"/projects/{pid}/export", headers={"Accept-Language": "de"},
                        data={"os": "windows", "root": "D:\\Videos\\Island 2026", "format": "premiere", "cut": "cut1",
                              "relink": json.dumps(relink)})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert 'filename="Island_2026_cut1_premiere.zip"' in r.headers["content-disposition"]
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        assert {"Film_cut1.xml", "IMPORT-PREMIERE.txt", "music/LIZENZEN.txt", "music/song.mp3"} <= names
        assert "music/LICENSES.txt" not in names                         # replaced by the German file
        timeline = json.loads(z.read("Film_cut1.xml"))
        assert timeline == {"os": "windows", "root": "D:\\Videos\\Island 2026", "relink": relink}
        readme = z.read("IMPORT-PREMIERE.txt").decode()
        assert "D:\\Videos\\Island 2026\\_cutlery" in readme and "Medien verknüpfen" in readme
        lic = z.read("music/LIZENZEN.txt").decode()
        assert "Lizenz: CC BY 4.0" in lic and "Quelle: https://example.org/song" in lic
        # nothing of the build is kept
        assert not any((cfg.work_dir / "projects" / pid / "tmp").iterdir())

    def test_english_package(self, client, cfg):
        pid = TestProjects().create(client)
        make_cut(cfg, pid)
        r = client.post(f"/projects/{pid}/export", headers={"Accept-Language": "en"},
                        data={"os": "macos", "root": "/Volumes/Card/Trip"})
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert "/Volumes/Card/Trip/_cutlery" in z.read("IMPORT-PREMIERE.txt").decode()
        assert "Licence: CC BY 4.0" in z.read("music/LICENSES.txt").decode()

    def test_failure_is_reported_in_the_ui_language(self, client, cfg, monkeypatch):
        pid = TestProjects().create(client)
        make_cut(cfg, pid)
        monkeypatch.setenv("FAKE_CUTTER_FAIL", "1")
        r = client.post(f"/projects/{pid}/export", headers={"Accept-Language": "de"},
                        data={"os": "macos", "root": "/x"})
        assert r.status_code == 422
        assert r.json()["message"].startswith("Das Paket konnte nicht erstellt werden: Error: simulated export failure")

    def test_bad_request_is_refused_before_cutter_runs(self, client, cfg):
        pid = TestProjects().create(client)
        make_cut(cfg, pid)
        r = client.post(f"/projects/{pid}/export", headers={"Accept-Language": "en"}, data={"os": "windows", "root": ""})
        assert r.status_code == 422 and r.json()["message"] == "Please enter the path of the footage folder."


class TestManifestApi:
    def test_only_uploaded_clips_and_the_folder_name(self, client, cfg, app):
        pid = TestProjects().create(client)
        cid = client.post(f"/api/projects/{pid}/clips", json=GOOD).json()["id"]
        assert client.get(f"/api/projects/{pid}/manifest").json()["clips"] == []
        app.state.projects.mark(pid, cid, "uploaded")
        assert client.post(f"/api/projects/{pid}/folder", json={"name": "Island 2026"}).status_code == 200
        m = client.get(f"/api/projects/{pid}/manifest").json()
        assert m["folder"] == "Island 2026"
        assert [c["fingerprint"] for c in m["clips"]] == [GOOD["fingerprint"]]
        assert client.post(f"/api/projects/{pid}/folder", json={"name": "../x"}).status_code == 422
