"""Browser tests: transcoding, sidecar and resumable upload with the generated test clips.

Run with:  pytest -m browser tests/browser
Chromium tests use the installed Google Chrome (channel "chrome"; it has the H.264 codecs), WebKit tests use
Playwright's WebKit. Each test starts its own Cutlery server on 127.0.0.1 with a temporary password.
"""
from __future__ import annotations

import json
import secrets
import shutil
import socket
import subprocess
import threading
import time

import pytest

pytestmark = pytest.mark.browser
playwright = pytest.importorskip("playwright.sync_api")

USABLE = ["Day 1/2024-06-01 10-15-00 h264.mp4", "Day 2/ntsc 29.97.mov", "Day 2/start timecode.mov",
          "Mono 44k/mono 44.1 kHz.mp4", "Trimmed/stream copy cut.mp4",
          "Phone/portrait rotated.mp4", "Phone/vfr.mp4",
          "Small/already small 512x288.mp4"]

def new_project(page, server, name="Test"):
    page.goto(server["url"] + "/projects/new")
    page.fill("#name", name)
    page.click("button[type=submit]")
    page.wait_for_url("**/setup")
    page.wait_for_function("() => window.cutlery && window.cutlery.caps")
    return page.url.split("/")[-2]


def rows(page):
    return page.evaluate("() => [...window.cutlery.rows.values()].map(r => ({path: r.relPath, status: r.status, "
                         "reason: r.reason || ''}))")


def wait_done(page, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        r = rows(page)
        if r and all(x["status"] in ("uploaded", "unsupported", "failed") for x in r):
            return {x["path"]: x for x in r}
        time.sleep(0.5)
    raise AssertionError(f"not finished: {rows(page)}")


def ffprobe(path, *args):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)],
                                     capture_output=True, text=True, check=True).stdout)


def test_transcode_and_upload(page, server, clips):
    pid = new_project(page, server)
    caps = page.evaluate("() => window.cutlery.caps")
    if not caps["avc"]:
        pytest.skip(f"{page.browser_name} cannot encode H.264 here")
    page.set_input_files("#folder-input", str(clips))
    result = wait_done(page)
    cfg = server["cfg"]

    # the broken file is reported, never skipped silently; HEVC only where the browser cannot decode it
    assert result["Broken/not a video.mts"]["status"] == "unsupported"
    assert "Phone/.landscape source.mp4" not in result          # hidden files are ignored
    hevc = result["Day 1/hevc main10.mov"]["status"]
    assert hevc in ("uploaded", "unsupported")
    usable = USABLE + (["Day 1/hevc main10.mov"] if hevc == "uploaded" else [])
    for name in usable:
        assert result[name]["status"] == "uploaded", result[name]
    assert page.locator("#unsupported-notice").is_visible()

    media = cfg.work_dir / "projects" / pid / "media"
    sidecars = cfg.data_dir / "projects" / pid / "sidecars"
    for name in usable:
        original, standin = clips / name, media / (name + ".mp4")
        sc = json.loads((sidecars / (name + ".json")).read_text())
        o = ffprobe(original, "-show_streams", "-show_format")
        ov = next(s for s in o["streams"] if s["codec_type"] == "video")
        oa = next(s for s in o["streams"] if s["codec_type"] == "audio")
        # sidecar = what ffprobe says about the original
        assert sc["video"]["r_frame_rate"] == ov["r_frame_rate"], name
        assert sc["video"]["avg_frame_rate"] == ov["avg_frame_rate"], name
        assert sc["video"]["nb_frames"] == int(ov["nb_frames"]), name
        assert (sc["video"]["width"], sc["video"]["height"]) == (ov["width"], ov["height"]), name
        assert abs(sc["duration"] - float(ov["duration"])) < 0.002, name
        assert abs(sc["format_duration"] - float(o["format"]["duration"])) < 0.002, name
        assert (sc["audio"]["channels"], sc["audio"]["sample_rate"]) == (oa["channels"], int(oa["sample_rate"]))
        ff_rot = next((int(d["rotation"]) for d in ov.get("side_data_list", []) if "rotation" in d), 0)
        assert sc["video"]["rotation"] == (-ff_rot) % 360, name          # sidecar: clockwise
        tc = ov.get("tags", {}).get("timecode") or o["format"].get("tags", {}).get("timecode")
        assert sc["timecode"] == tc, name
        assert sc["size"] == original.stat().st_size
        # stand-in: upright, long side 640 (never upscaled), H.264 + AAC 48 kHz, same frames at the same times
        s = ffprobe(standin, "-show_streams")
        sv = next(x for x in s["streams"] if x["codec_type"] == "video")
        sa = next(x for x in s["streams"] if x["codec_type"] == "audio")
        assert sv["codec_name"] == "h264" and sa["codec_name"] == "aac" and sa["sample_rate"] == "48000"
        assert sa["channels"] == oa["channels"]
        assert not any("rotation" in d for d in sv.get("side_data_list", []))
        upright = (ov["height"], ov["width"]) if ff_rot % 180 else (ov["width"], ov["height"])
        assert max(sv["width"], sv["height"]) == min(640, max(upright)), name
        assert (sv["width"] > sv["height"]) == (upright[0] > upright[1]), name
        # the frames that are shown (decoded), at the times they are shown
        pts = lambda f: sorted(float(x["pts_time"]) for x in ffprobe(f, "-select_streams", "v:0",
                                                                      "-show_entries", "frame=pts_time")["frames"])
        po, ps = pts(original), pts(standin)
        if sc["video"]["edit_list"]:
            # an edit list that starts between two frames: ffmpeg shows the first kept frame at 0, Mediabunny keeps the
            # exact offset (docs/m1-feasibility.md). Up to half a frame and one frame more are tolerated.
            frame = 1 / float(ov["r_frame_rate"].split("/")[0]) * float(ov["r_frame_rate"].split("/")[1])
            assert abs(len(po) - len(ps)) <= 1, name
            assert max(min(abs(a - b) for b in ps) for a in po) <= frame / 2 + 0.001, name
        else:
            assert len(po) == len(ps), name
            assert max(abs(a - b) for a, b in zip(po, ps)) < 0.001, name

    sc = json.loads((sidecars / "Day 2/ntsc 29.97.mov.json").read_text())
    assert sc["timecode"] == "01:02:03;04" and sc["video"]["r_frame_rate"] == "30000/1001"
    sc = json.loads((sidecars / "Phone/vfr.mp4.json").read_text())
    assert sc["video"]["vfr"] is True
    sc = json.loads((sidecars / "Day 1/hevc main10.mov.json").read_text())
    assert sc["video"]["bit_depth"] == 10
    # DJI flight data next to a clip is uploaded too
    assert (sidecars / "Day 1/2024-06-01 10-15-00 h264.mp4.srt").read_text().startswith("1\n")
    manifest = json.loads((cfg.data_dir / "projects" / pid / "manifest.json").read_text())
    assert len(manifest["clips"]) == len(result)


def test_reload_continues_without_redoing_finished_clips(page, server, clips, tmp_path):
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough for the resume test")
    folder = tmp_path / "footage"
    for name in ("Day 1/2024-06-01 10-15-00 h264.mp4", "Day 2/start timecode.mov", "Phone/vfr.mp4"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(clips / name, folder / name)
    pid = new_project(page, server)
    page.set_input_files("#folder-input", str(folder))
    end = time.time() + 120
    while time.time() < end and not any(r["status"] == "uploaded" for r in rows(page)):
        time.sleep(0.2)
    page.reload()
    page.wait_for_function("() => window.cutlery && window.cutlery.caps")
    before = {r["path"]: r["status"] for r in rows(page)}
    assert "uploaded" in before.values()          # the server remembers finished clips after the reload
    seen = {}
    page.set_input_files("#folder-input", str(folder))
    end = time.time() + 120
    while time.time() < end:
        r = rows(page)
        for x in r:
            seen.setdefault(x["path"], set()).add(x["status"])
        if all(x["status"] == "uploaded" for x in r):
            break
        time.sleep(0.05)
    for path, status in before.items():
        if status == "uploaded":
            assert "transcoding" not in seen.get(path, set()), path
    clips_state = {c["rel_path"]: c["state"] for c in
                   page.evaluate(f"() => fetch('/api/projects/{pid}/clips').then(r => r.json())")["clips"]}
    assert set(clips_state.values()) == {"uploaded"}


def test_a_worker_that_does_not_load_is_reported_not_silent(page, server, clips, tmp_path):
    """A download of the transcoding library cut short (seen behind a reverse proxy): the page says so instead of
    ignoring the chosen folder."""
    def truncated(route):
        body = route.fetch().body()
        route.fulfill(status=200, body=body[:147456], headers={"content-type": "text/javascript"})
    page.context.route("**/mediabunny-1.64.0.min.mjs", truncated)
    from conftest import new_project_api
    new_project_api(page, server)
    page.wait_for_selector("#error-notice:not([hidden])", timeout=40000)
    text = page.inner_text("#error-notice")
    assert "reload" in text.lower() or "neu" in text.lower(), text
    assert page.is_disabled("#folder-input")


def test_a_folder_without_videos_says_so(page, server, tmp_path):
    folder = tmp_path / "Holiday"
    folder.mkdir()
    (folder / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    new_project(page, server)
    page.set_input_files("#folder-input", str(folder))
    page.wait_for_selector("#error-notice:not([hidden])", timeout=10000)
    assert "1" in page.inner_text("#error-notice")


def test_several_workers_transcode_side_by_side(page, server, clips):
    """?parallel=2 (for measurements on other machines): two workers, every usable clip still arrives."""
    pid = new_project(page, server)
    if not page.evaluate("() => window.cutlery.caps")["avc"]:
        pytest.skip(f"{page.browser_name} cannot encode H.264 here")
    page.goto(page.url + "?parallel=2")
    page.wait_for_function("() => window.cutlery && window.cutlery.caps")
    page.set_input_files("#folder-input", str(clips))
    result = wait_done(page)
    for name in USABLE:
        assert result[name]["status"] == "uploaded", result[name]
    media = server["cfg"].work_dir / "projects" / pid / "media"
    assert len([p for p in media.rglob("*.mp4")]) >= len(USABLE)


def test_a_stuck_upload_can_be_started_again(page, server, clips, tmp_path):
    """An upload that hangs (no answer, no error) is not the end: "start upload again" goes through the folder once
    more, skips what is done and finishes the rest."""
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough for the restart test")
    folder = tmp_path / "footage"
    names = ("Day 1/2024-06-01 10-15-00 h264.mp4", "Day 2/start timecode.mov", "Phone/vfr.mp4")
    for name in names:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(clips / name, folder / name)
    hung = []
    page.context.route("**/tus/**", lambda route: hung.append(route))    # never answered: requests just sit there
    new_project(page, server)
    assert page.is_hidden("#upload-restart")
    page.set_input_files("#folder-input", str(folder))
    end = time.time() + 120
    while time.time() < end and not (hung and any(r["status"] == "uploading" for r in rows(page))):
        page.wait_for_timeout(200)         # (lets Playwright run the route handler)
    assert hung, rows(page)
    page.wait_for_selector("#upload-restart:visible")
    page.context.unroute("**/tus/**")
    page.click("#upload-restart")
    result = wait_done(page)
    assert {result[n]["status"] for n in names} == {"uploaded"}, [r["reason"] for r in result.values()]
    assert page.is_hidden("#upload-restart")
