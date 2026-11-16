"""Sidecar metadata: what the browser measured on the ORIGINAL file of a clip.

The browser writes one sidecar per clip (format "cutlery.sidecar/1", documented in docs/sidecar.md). The server
validates it, stores it in the project's data tree and keeps a manifest of all clips for matching the originals
again at export.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata

FORMAT = "cutlery.sidecar/1"
MAX_BYTES = 256 * 1024
FINGERPRINT_CHUNK = 1024 * 1024          # SHA-256 of the first and the last MiB
RATE = re.compile(r"^\d{1,7}/\d{1,7}$")
TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2}[:;]\d{2,3}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SidecarError(ValueError):
    """The sidecar is not acceptable; the text names the field."""


def check_rel_path(rel_path) -> str:
    """A relative path inside the footage folder, with '/' separators. Rejects anything that could escape it."""
    if not isinstance(rel_path, str) or not rel_path:
        raise SidecarError("rel_path is missing")
    if len(rel_path.encode("utf-8")) > 1024 or "\x00" in rel_path or "\\" in rel_path:
        raise SidecarError("rel_path is too long or contains forbidden characters")
    if any(unicodedata.category(c) == "Cc" for c in rel_path):
        raise SidecarError("rel_path contains control characters")
    if rel_path.startswith("/") or re.match(r"^[A-Za-z]:", rel_path):
        raise SidecarError("rel_path must be relative")
    parts = rel_path.split("/")
    for part in parts:
        if part in ("", ".", "..") or len(part.encode("utf-8")) > 255:
            raise SidecarError(f"rel_path has an invalid part: '{part}'")
    if parts[-1].startswith("."):
        raise SidecarError("rel_path names a hidden file")
    return rel_path


def fingerprint_key(fp: dict) -> str:
    """One string for a fingerprint: size plus both hashes."""
    return f"{fp['size']}:{fp['head_sha256']}:{fp['tail_sha256']}"


def _num(obj, key, *, lo=0.0, hi=math.inf, required=True, integer=False):
    value = obj.get(key)
    if value is None:
        if required:
            raise SidecarError(f"{key} is missing")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SidecarError(f"{key} must be a number")
    if integer and int(value) != value:
        raise SidecarError(f"{key} must be a whole number")
    if not lo <= value <= hi:
        raise SidecarError(f"{key} is out of range")
    return value


def _str(obj, key, *, required=False, maxlen=256, pattern=None):
    value = obj.get(key)
    if value is None:
        if required:
            raise SidecarError(f"{key} is missing")
        return None
    if not isinstance(value, str) or len(value) > maxlen:
        raise SidecarError(f"{key} must be a text of at most {maxlen} characters")
    if pattern and not pattern.match(value):
        raise SidecarError(f"{key} has an invalid format: '{value}'")
    return value


def validate(raw: bytes | str | dict) -> dict:
    """Parse and check a sidecar. Returns the dict; raises SidecarError."""
    if isinstance(raw, (bytes, str)):
        if len(raw) > MAX_BYTES:
            raise SidecarError("sidecar is too large")
        try:
            sc = json.loads(raw)
        except ValueError as e:
            raise SidecarError(f"sidecar is not valid JSON ({e})")
    else:
        sc = raw
    if not isinstance(sc, dict) or sc.get("format") != FORMAT:
        raise SidecarError(f"format must be '{FORMAT}'")
    check_rel_path(sc.get("rel_path"))
    name = _str(sc, "file", required=True)
    if name != sc["rel_path"].rsplit("/", 1)[-1]:
        raise SidecarError("file must be the last part of rel_path")
    _num(sc, "size", lo=1, integer=True)
    _str(sc, "mtime", required=True, maxlen=40)
    _str(sc, "container", maxlen=40)
    _str(sc, "creation_time", maxlen=40)
    _str(sc, "timecode", pattern=TIMECODE)
    _str(sc, "gps", maxlen=64)

    fp = sc.get("fingerprint")
    if not isinstance(fp, dict):
        raise SidecarError("fingerprint is missing")
    if fp.get("size") != sc["size"]:
        raise SidecarError("fingerprint.size must equal size")
    for key in ("head_sha256", "tail_sha256"):
        _str(fp, key, required=True, pattern=HEX64)

    unsupported = sc.get("unsupported")
    if unsupported is not None:
        if not isinstance(unsupported, dict):
            raise SidecarError("unsupported must be an object")
        _str(unsupported, "reason", required=True, maxlen=64)
        _str(unsupported, "detail", maxlen=500)
    else:
        _num(sc, "duration", lo=0.0, hi=48 * 3600)

    video = sc.get("video")
    if video is not None:
        if not isinstance(video, dict):
            raise SidecarError("video must be an object")
        _str(video, "codec", required=True, maxlen=32)
        _str(video, "codec_string", maxlen=64)
        _num(video, "width", lo=1, hi=16384, integer=True, required=unsupported is None)
        _num(video, "height", lo=1, hi=16384, integer=True, required=unsupported is None)
        if video.get("rotation", 0) not in (0, 90, 180, 270):
            raise SidecarError("video.rotation must be 0, 90, 180 or 270")
        for key in ("r_frame_rate", "avg_frame_rate"):
            _str(video, key, required=unsupported is None, pattern=RATE)
        _num(video, "bit_depth", lo=1, hi=16, integer=True, required=False)
        _num(video, "nb_frames", lo=0, integer=True, required=False)
    elif unsupported is None:
        raise SidecarError("video is missing")

    audio = sc.get("audio")
    if audio is not None:
        if not isinstance(audio, dict):
            raise SidecarError("audio must be an object")
        _str(audio, "codec", required=True, maxlen=32)
        _num(audio, "channels", lo=1, hi=64, integer=True)
        _num(audio, "sample_rate", lo=1000, hi=768000, integer=True)

    tags = sc.get("tags", {})
    if not isinstance(tags, dict) or len(tags) > 200:
        raise SidecarError("tags must be an object with at most 200 entries")
    for k, v in tags.items():
        if not isinstance(k, str) or len(k) > 128 or not isinstance(v, str) or len(v) > 4096:
            raise SidecarError("tags must map short names to texts")

    tel = sc.get("telemetry", {})
    if not isinstance(tel, dict):
        raise SidecarError("telemetry must be an object")
    return sc


def rate(text: str | None) -> float | None:
    if not text:
        return None
    num, den = text.split("/")
    return int(num) / int(den) if int(den) else None


def standin_matches(sidecar: dict, standin_duration: float, standin_frames: int | None = None) -> str | None:
    """cutter's proxy contract: same length within 2 frames + 0.02 s. Returns a problem text or None."""
    duration = sidecar.get("duration")
    if not duration:
        return None
    fps = rate(sidecar.get("video", {}).get("avg_frame_rate")) or 25.0
    tolerance = 2 / fps + 0.02
    if abs(standin_duration - duration) > tolerance:
        return (f"the 360p copy is {standin_duration:.3f} s long, the original {duration:.3f} s "
                f"(allowed difference {tolerance:.3f} s)")
    nb = sidecar.get("video", {}).get("nb_frames")
    if nb and standin_frames is not None and abs(standin_frames - nb) > 2:
        return f"the 360p copy has {standin_frames} frames, the original {nb}"
    return None


def manifest_entry(clip_id: str, sc: dict) -> dict:
    return {"id": clip_id, "rel_path": sc["rel_path"], "file": sc["file"], "size": sc["size"],
            "mtime": sc["mtime"], "fingerprint": sc["fingerprint"], "duration": sc.get("duration")}
