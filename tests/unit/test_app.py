import base64
import hashlib
import json
import subprocess

import pytest

from cutlery import i18n
from conftest import PASSWORD, auth_header
from test_sidecar import GOOD, variant


def b64(s):
    return base64.b64encode(s.encode()).decode()


TUS = {"Tus-Resumable": "1.0.0"}


class TestAuth:
    def test_everything_needs_a_password(self, app):
        from starlette.testclient import TestClient

        with TestClient(app) as c:
            for path in ("/", "/api/status", "/static/css/cutlery.css", "/healthz", "/tus/", "/i18n/en.json"):
                r = c.get(path)
                assert r.status_code == 401, path
                assert r.headers["www-authenticate"].startswith("Basic")

    def test_correct_password(self, client):
        assert client.get("/").status_code == 200

    def test_lockout_after_failures(self, app):
        from starlette.testclient import TestClient

        from cutlery.auth import MAX_FAILURES

        with TestClient(app) as c:
            for _ in range(MAX_FAILURES):
                assert c.get("/", headers=auth_header(password="wrong")).status_code == 401
            r = c.get("/", headers=auth_header())
            assert r.status_code == 429 and "Retry-After" in r.headers

    def test_health_check_token_only_from_loopback(self, app):
        from starlette.testclient import TestClient

        with TestClient(app, client=("127.0.0.1", 5000)) as c:
            assert c.get("/healthz", headers={"X-Cutlery-Health": "health-token"}).status_code == 200
            assert c.get("/healthz", headers={"X-Cutlery-Health": "wrong"}).status_code == 401
            assert c.get("/", headers={"X-Cutlery-Health": "health-token"}).status_code == 401
        with TestClient(app, client=("192.0.2.7", 5000)) as c:
            assert c.get("/healthz", headers={"X-Cutlery-Health": "health-token"}).status_code == 401

    def test_no_password_set(self, cfg):
        from starlette.testclient import TestClient

        from cutlery.app import create_app

        with TestClient(create_app(cfg)) as c:
            r = c.get("/", headers=auth_header())
            assert r.status_code == 503 and "set-password" in r.text

    def test_single_user(self, cfg):
        from cutlery.auth import set_single_user
        from cutter.review_app import set_password

        set_password.store(str(cfg.htpasswd), "old", "old-password-1")
        set_password.store(str(cfg.htpasswd), "other", "old-password-2")
        set_single_user(cfg.htpasswd, "me", generate=True)
        assert set_password.names(str(cfg.htpasswd)) == ["me"]
        assert oct(cfg.htpasswd.stat().st_mode & 0o777) == "0o600"


class TestSecurityHeaders:
    def test_csp_and_friends(self, client):
        h = client.get("/").headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"

    def test_cross_site_post_is_refused(self, client):
        r = client.post("/projects/new", data={"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
        r = client.post("/projects/new", data={"name": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_same_origin_post_passes(self, client):
        r = client.post("/projects/new", data={"name": "x"}, headers={"Origin": "http://testserver"},
                        follow_redirects=False)
        assert r.status_code == 303


class TestLanguage:
    def test_accept_language(self, client):
        assert 'lang="de"' in client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}).text
        assert 'lang="en"' in client.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).text

    def test_switch_is_remembered(self, client):
        r = client.get("/lang/de?next=/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert 'lang="de"' in client.get("/", headers={"Accept-Language": "en"}).text

    def test_switch_does_not_redirect_offsite(self, client):
        assert client.get("/lang/en?next=//evil.example", follow_redirects=False).headers["location"] == "/"


class TestProjects:
    def create(self, client, name="Iceland 2026"):
        r = client.post("/projects/new", data={"name": name}, follow_redirects=False)
        assert r.status_code == 303
        return r.headers["location"].split("/")[2]

    def test_create_and_list(self, client):
        pid = self.create(client)
        assert "Iceland 2026" in client.get("/").text
        assert client.get(f"/projects/{pid}/footage").status_code == 200

    def test_register_clip_twice_returns_same_id(self, client, cfg):
        pid = self.create(client)
        a = client.post(f"/api/projects/{pid}/clips", json=GOOD).json()
        b = client.post(f"/api/projects/{pid}/clips", json=GOOD).json()
        assert a["id"] == b["id"] == "c0001"
        assert a["need"] == {"standin": True, "gpmd": False, "srt": False}
        sc = cfg.data_dir / "projects" / pid / "sidecars" / (GOOD["rel_path"] + ".json")
        assert json.loads(sc.read_text())["clip_id"] == "c0001"
        manifest = json.loads((cfg.data_dir / "projects" / pid / "manifest.json").read_text())
        assert manifest["clips"][0]["fingerprint"] == GOOD["fingerprint"]

    def test_renamed_file_keeps_its_clip(self, client, cfg):
        pid = self.create(client)
        client.post(f"/api/projects/{pid}/clips", json=GOOD)
        moved = variant(rel_path="Day 1/renamed.MP4", file="renamed.MP4")
        r = client.post(f"/api/projects/{pid}/clips", json=moved).json()
        assert r["id"] == "c0001" and r["rel_path"] == "Day 1/renamed.MP4"
        assert (cfg.data_dir / "projects" / pid / "sidecars" / "Day 1/renamed.MP4.json").exists()

    def test_changed_file_replaces_its_clip(self, client):
        pid = self.create(client)
        client.post(f"/api/projects/{pid}/clips", json=GOOD)
        changed = variant(fingerprint__head_sha256="c" * 64)
        r = client.post(f"/api/projects/{pid}/clips", json=changed).json()
        assert r["id"] == "c0002"
        assert [c["id"] for c in client.get(f"/api/projects/{pid}/clips").json()["clips"]] == ["c0002"]

    def test_unsupported_clip(self, client):
        pid = self.create(client)
        sc = variant(duration=None, video={"codec": "hevc"}, unsupported={"reason": "video_codec", "detail": "hev1"})
        r = client.post(f"/api/projects/{pid}/clips", json=sc).json()
        assert r["state"] == "unsupported" and r["need"]["standin"] is False

    def test_bad_sidecar_is_refused_with_reason(self, client):
        pid = self.create(client)
        r = client.post(f"/api/projects/{pid}/clips", json=variant(rel_path="../x.MP4", file="x.MP4"))
        assert r.status_code == 422 and "rel_path" in r.json()["error"]

    def test_telemetry(self, client, cfg):
        pid = self.create(client)
        sc = variant(telemetry={"gpmd": True, "srt": "GOPR0319.SRT"})
        cid = client.post(f"/api/projects/{pid}/clips", json=sc).json()["id"]
        assert client.put(f"/api/projects/{pid}/clips/{cid}/telemetry/gpmd", content=b"DEVC\x00\x01").status_code == 200
        assert client.put(f"/api/projects/{pid}/clips/{cid}/telemetry/srt", content="1\n".encode()).status_code == 200
        assert client.put(f"/api/projects/{pid}/clips/{cid}/telemetry/srt", content=b"\xff\xfe").status_code == 422
        assert client.post(f"/api/projects/{pid}/clips", json=sc).json()["need"] == {
            "standin": True, "gpmd": False, "srt": False}
        assert (cfg.data_dir / "projects" / pid / "sidecars" / (GOOD["rel_path"] + ".gpmd")).read_bytes() == b"DEVC\x00\x01"

    def test_delete_needs_the_name_and_removes_everything(self, client, cfg):
        pid = self.create(client, "Keep me")
        client.post(f"/api/projects/{pid}/clips", json=GOOD)
        assert client.post(f"/projects/{pid}/delete", data={"confirm": "wrong"}).status_code == 200
        assert (cfg.data_dir / "projects" / pid).exists()
        r = client.post(f"/projects/{pid}/delete", data={"confirm": "Keep me"}, follow_redirects=False)
        assert r.status_code == 303
        assert not (cfg.data_dir / "projects" / pid).exists()
        assert not (cfg.work_dir / "projects" / pid).exists()
        assert client.get(f"/api/projects/{pid}/clips").status_code == 404


def ffprobe_sidecar(path, rel_path):
    """A sidecar for a test clip, built from ffprobe (what the browser measures)."""
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(path)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    size = path.stat().st_size
    data = path.read_bytes()
    # the rotated test clip is a stream copy of another one: only the container differs, so hash like the browser does
    fp = {"size": size, "head_sha256": hashlib.sha256(data[:1 << 20]).hexdigest(),
          "tail_sha256": hashlib.sha256(data[-(1 << 20):]).hexdigest()}
    return variant(rel_path=rel_path, file=rel_path.split("/")[-1], size=size, duration=float(v["duration"]),
                   format_duration=float(info["format"]["duration"]),
                   fingerprint=fp,
                   video={"codec": "avc", "width": v["width"], "height": v["height"], "rotation": 0,
                          "r_frame_rate": v["r_frame_rate"], "avg_frame_rate": v["avg_frame_rate"],
                          "nb_frames": int(v["nb_frames"])},
                   audio={"codec": "aac", "channels": a["channels"], "sample_rate": int(a["sample_rate"])})


def make_standin(src, dst, *, duration=None):
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    if duration:
        args += ["-t", str(duration)]
    args += ["-vf", "scale=640:-2", "-c:v", "libx264", "-c:a", "aac", "-ar", "48000", str(dst)]
    subprocess.run(args, check=True)
    return dst.read_bytes()


class TestTus:
    def setup_project(self, client, clips, tmp_path, *, short=False):
        pid = TestProjects().create(client)
        original = clips / "Day 1" / "2024-06-01 10-15-00 h264.mp4"
        sc = ffprobe_sidecar(original, "Day 1/2024-06-01 10-15-00 h264.mp4")
        cid = client.post(f"/api/projects/{pid}/clips", json=sc).json()["id"]
        data = make_standin(original, tmp_path / "standin.mp4", duration=1.0 if short else None)
        return pid, cid, data

    def create_upload(self, client, pid, cid, length):
        r = client.post("/tus/", headers={**TUS, "Upload-Length": str(length),
                                          "Upload-Metadata": f"project {b64(pid)},clip {b64(cid)}"})
        assert r.status_code == 201, r.text
        return r.headers["location"]

    def patch(self, client, url, offset, chunk):
        return client.patch(url, content=chunk, headers={**TUS, "Upload-Offset": str(offset),
                                                           "Content-Type": "application/offset+octet-stream"})

    def test_options(self, client):
        r = client.options("/tus/")
        assert r.headers["tus-version"] == "1.0.0" and "creation" in r.headers["tus-extension"]

    def test_resume_and_finish(self, client, clips, cfg, tmp_path):
        pid, cid, data = self.setup_project(client, clips, tmp_path)
        url = self.create_upload(client, pid, cid, len(data))
        half = len(data) // 2
        assert self.patch(client, url, 0, data[:half]).headers["upload-offset"] == str(half)
        # a reload: the browser asks where to continue
        assert client.head(url, headers=TUS).headers["upload-offset"] == str(half)
        assert self.patch(client, url, 0, data[:10]).status_code == 409          # wrong offset
        r = self.patch(client, url, half, data[half:])
        assert r.status_code == 204, r.text
        clip = client.get(f"/api/projects/{pid}/clips").json()["clips"][0]
        assert clip["state"] == "uploaded" and clip["standin_size"] == len(data)
        standin = cfg.work_dir / "projects" / pid / "media" / "Day 1/2024-06-01 10-15-00 h264.mp4.mp4"
        assert standin.read_bytes() == data
        assert client.head(url, headers=TUS).status_code == 404
        # no second upload for a finished clip
        r = client.post("/tus/", headers={**TUS, "Upload-Length": "10",
                                          "Upload-Metadata": f"project {b64(pid)},clip {b64(cid)}"})
        assert r.status_code == 409

    def test_wrong_length_standin_is_rejected(self, client, clips, cfg, tmp_path):
        pid, cid, data = self.setup_project(client, clips, tmp_path, short=True)
        url = self.create_upload(client, pid, cid, len(data))
        r = self.patch(client, url, 0, data)
        assert r.status_code == 422 and "360p copy is 1.0" in r.text
        clip = client.get(f"/api/projects/{pid}/clips").json()["clips"][0]
        assert clip["state"] == "failed" and "360p copy" in clip["reason"]
        # a failed clip may be uploaded again
        self.create_upload(client, pid, cid, len(data))

    def test_more_bytes_than_announced(self, client, clips, tmp_path):
        pid, cid, data = self.setup_project(client, clips, tmp_path)
        url = self.create_upload(client, pid, cid, 100)
        assert self.patch(client, url, 0, data[:200]).status_code == 413

    def test_unknown_clip(self, client):
        r = client.post("/tus/", headers={**TUS, "Upload-Length": "10",
                                          "Upload-Metadata": f"project {b64('nope')},clip {b64('c0001')}"})
        assert r.status_code == 404

    def test_terminate(self, client, clips, tmp_path, cfg):
        pid, cid, data = self.setup_project(client, clips, tmp_path)
        url = self.create_upload(client, pid, cid, len(data))
        assert client.delete(url, headers=TUS).status_code == 204
        assert not any((cfg.work_dir / "uploads").iterdir())


class TestTranslations:
    def test_every_key_in_every_language(self):
        cats = i18n.catalogs()
        assert {"en", "de"} <= set(cats)
        for code, cat in cats.items():
            assert set(cat) == set(cats["en"]), f"{code}: {set(cat) ^ set(cats['en'])}"

    def test_same_placeholders(self):
        cats = i18n.catalogs()
        for key, text in cats["en"].items():
            want = set(i18n.PLACEHOLDER.findall(text))
            for code, cat in cats.items():
                assert set(i18n.PLACEHOLDER.findall(cat[key])) == want, f"{code}: {key}"

    def test_product_name_is_never_translated(self):
        for cat in i18n.catalogs().values():
            for text in cat.values():
                assert "Besteck" not in text

    def test_templates_use_only_known_keys(self):
        import re

        from cutlery.app import PKG

        known = set(i18n.catalogs()["en"])
        used = set()
        for f in (PKG / "templates").iterdir():
            used |= set(re.findall(r"""\bt\('([a-z0-9_.]+)'""", f.read_text()))
        for f in (PKG / "static" / "js").iterdir():
            used |= set(re.findall(r"""\bt\('([a-z0-9_.]+)'""", f.read_text()))
        used = {k for k in used if not k.endswith(".")}      # prefixes of keys built at run time
        assert used and used - known == set()
