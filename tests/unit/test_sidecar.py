import copy
import json

import pytest

from cutlery import sidecar
from cutlery.sidecar import SidecarError, check_rel_path, validate

GOOD = {
    "format": "cutlery.sidecar/1",
    "file": "2011-08-24 GOPR0319.MP4",
    "rel_path": "GOPRO/2011-08-24 GOPR0319.MP4",
    "size": 17236428,
    "mtime": "2011-08-24T19:47:12.000Z",
    "container": "MP4",
    "duration": 16.44,
    "format_duration": 16.44,
    "creation_time": "2011-08-24T19:47:12.000Z",
    "timecode": None,
    "gps": "+52.5163+013.3777/",
    "video": {"codec": "avc", "codec_string": "avc1.4d401f", "width": 1280, "height": 720, "rotation": 0,
              "r_frame_rate": "25/1", "avg_frame_rate": "25/1", "nb_frames": 411, "vfr": False, "bit_depth": 8},
    "audio": {"codec": "aac", "channels": 2, "sample_rate": 48000},
    "tags": {"encoder": "GoPro AVC"},
    "telemetry": {"gpmd": False, "srt": None},
    "fingerprint": {"size": 17236428, "head_sha256": "a" * 64, "tail_sha256": "b" * 64},
}


def variant(**changes):
    sc = copy.deepcopy(GOOD)
    for key, value in changes.items():
        target = sc
        *path, last = key.split("__")
        for p in path:
            target = target[p]
        if value is ...:
            del target[last]
        else:
            target[last] = value
    return sc


def test_good_sidecar_passes():
    assert validate(json.dumps(GOOD))["rel_path"] == GOOD["rel_path"]


@pytest.mark.parametrize("rel_path", ["../etc/passwd", "a/../b.mp4", "/abs/x.mp4", "C:/x.mp4", "a//b.mp4",
                                      "a\\b.mp4", "a/./b.mp4", "dir/.hidden.mp4", "a\x00b.mp4", "a\nb.mp4", "",
                                      "x" * 300 + ".mp4"])
def test_unsafe_rel_paths_are_refused(rel_path):
    with pytest.raises(SidecarError):
        check_rel_path(rel_path)


@pytest.mark.parametrize("rel_path", ["clip.mp4", "Tag 3/IMG_4821 Gletscher.MOV", "Ünïcödé/Straße.mov",
                                      "Dir.With.Dots/a b c.mp4", "日本/動画.mp4"])
def test_normal_rel_paths_pass(rel_path):
    assert check_rel_path(rel_path) == rel_path


@pytest.mark.parametrize("changes, message", [
    ({"format": "other/1"}, "format"),
    ({"file": "other.MP4"}, "last part of rel_path"),
    ({"size": -1}, "size"),
    ({"fingerprint__size": 1}, "fingerprint.size"),
    ({"fingerprint__head_sha256": "xyz"}, "head_sha256"),
    ({"video__rotation": 45}, "rotation"),
    ({"video__r_frame_rate": "25"}, "r_frame_rate"),
    ({"video": ...}, "video is missing"),
    ({"duration": float("nan")}, "duration"),
    ({"timecode": "1:2:3:4"}, "timecode"),
    ({"tags": {"k": 5}}, "tags"),
    ({"audio__channels": 0}, "channels"),
])
def test_bad_fields_are_named(changes, message):
    with pytest.raises(SidecarError, match=message):
        validate(variant(**changes))


def test_unsupported_clip_needs_no_rates_or_duration():
    sc = variant(duration=None, video={"codec": "hevc"}, unsupported={"reason": "video_codec", "detail": "hev1"})
    assert validate(sc)["unsupported"]["reason"] == "video_codec"


def test_drop_frame_timecode_passes():
    assert validate(variant(timecode="01:02:03;04"))["timecode"] == "01:02:03;04"


def test_too_large_is_refused():
    with pytest.raises(SidecarError, match="too large"):
        validate(b"{" + b" " * sidecar.MAX_BYTES + b"}")


def test_fingerprint_key():
    assert sidecar.fingerprint_key(GOOD["fingerprint"]) == f"17236428:{'a' * 64}:{'b' * 64}"


class TestStandinContract:
    def test_same_length_passes(self):
        assert sidecar.standin_matches(GOOD, 16.44, 411) is None

    def test_within_two_frames(self):
        assert sidecar.standin_matches(GOOD, 16.44 + 2 / 25, 411) is None

    def test_too_short(self):
        assert "360p copy is 15.000 s" in sidecar.standin_matches(GOOD, 15.0, 375)

    def test_frames_missing(self):
        assert "frames" in sidecar.standin_matches(GOOD, 16.44, 400)
