"""The cutter project folder of a Cutlery project.

DATA/projects/<id>/ is a cutter project (it holds cutter.toml). cutter reads the 360p stand-ins from
WORK/projects/<id>/media, takes the originals' metadata from DATA/projects/<id>/sidecars and writes everything it
regenerates (clips.json, transcripts, contact sheets, image analysis) to WORK/projects/<id>/analysis.
"""
from __future__ import annotations

import json
import pathlib
import re

from cutter.frame import FORMATS

from .storage import ProjectPaths, write_atomic


EXPORT_HEIGHT = 360
FRAMES = tuple(FORMATS)                 # 16:9, 9:16, 4:5, 3:4, 1:1, 2:3, 4:3, 21:9 (cutter docs/formats.md, Frames)
DEFAULT_FRAME = "16:9"


def analysis_dir(paths: ProjectPaths) -> pathlib.Path:
    return paths.work / "analysis"


def _settings(paths: ProjectPaths) -> dict:
    try:
        d = json.loads((paths.data / "cutlery.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def frame(paths: ProjectPaths) -> str:
    """The project's picture format: a film (16:9), a reel (9:16), a feed post (4:5, 1:1) …"""
    f = _settings(paths).get("frame")
    return f if f in FRAMES else DEFAULT_FRAME


def set_frame(paths: ProjectPaths, value: str) -> bool:
    """Change the picture format (stored in cutlery.json, written to cutter.toml [timeline] frame). The cuts made so
    far keep theirs: their edit lists get the old one ("frame"), unless they have one. Returns whether it changed."""
    if value not in FRAMES:
        raise ValueError(f"frame must be one of {', '.join(FRAMES)}")
    old = frame(paths)
    if value == old and (paths.data / "cutlery.json").exists():
        return False
    for d in (paths.data / "cuts").glob("*"):
        edit = d / "edit.json"
        try:
            data = json.loads(edit.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and not data.get("frame"):
            data["frame"] = old
            write_atomic(edit, (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    write_atomic(paths.data / "cutlery.json",
                 (json.dumps(dict(_settings(paths), frame=value), indent=1) + "\n").encode("utf-8"))
    return value != old


def render(paths: ProjectPaths, title: str, language: str = "") -> str:
    """cutter.toml for the project. Paths are absolute, so cutter needs no knowledge of the two trees."""
    a = analysis_dir(paths)
    q = json.dumps                      # a JSON string is a valid TOML basic string
    return "\n".join([
        "# Written by Cutlery; changes are overwritten.",
        f"title = {q(title, ensure_ascii=False)}",
        f"language = {q(language)}",
        "",
        "[media]",
        f"standins = {q(str(paths.media))}",
        "",
        "[standins]",
        f"sidecars = {q(str(paths.sidecars))}",
        "",
        "[paths]",
        f"clips = {q(str(a / 'clips.json'))}",
        f"transcripts = {q(str(a / 'transcripts'))}",
        f"image_analysis = {q(str(a / 'image'))}",
        f"proxies = {q(str(a / 'proxies'))}",
        # per cut: edit list, decisions and plan stay in the data tree (cuts/<cut>/…, cutter's default);
        # what cutter builds from them is regenerable
        f"timeline = {q(str(paths.work / 'cuts' / '{cut}' / '{cut}'))}",
        f"work = {q(str(paths.work / 'cuts' / '{cut}' / 'work'))}",
        f"filmcraft_project = {q(str(paths.work / 'cuts' / '{cut}' / '{cut}.fcproj'))}",
        f"exports = {q(str(paths.work / 'exports'))}",
        # the review: comments stay with the project (cutter's default .cutter/review/data), renders are regenerable
        f"review_media = {q(str(review_media_dir(paths)))}",
        "",
        # the picture format of the cuts; a cut's edit.json "frame" wins (cuts made before a change keep theirs)
        "[timeline]",
        f"frame = {q(frame(paths))}",
        "",
        # renders on the server come from the 360p stand-ins: render at that size, not upscaled (the film in full
        # resolution is made from the originals on the user's computer, see the export)
        "[filmcraft]",
        f"export_height = {EXPORT_HEIGHT}",
        "",
    ])


def review_media_dir(paths: ProjectPaths) -> pathlib.Path:
    return paths.work / "review"


def write(paths: ProjectPaths, title: str, language: str = "") -> pathlib.Path:
    target = paths.data / "cutter.toml"
    write_atomic(target, render(paths, title, language).encode("utf-8"))
    return target


def cuts(paths: ProjectPaths) -> list[str]:
    """The cuts that have an edit list, in natural order (cut2 before cut10)."""
    found = [d.name for d in (paths.data / "cuts").glob("*") if (d / "edit.json").is_file()]
    return sorted(found, key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])


def clips_json(paths: ProjectPaths) -> list[dict]:
    try:
        return json.loads((analysis_dir(paths) / "clips.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def progress(paths: ProjectPaths) -> dict:
    """What cutter has produced so far: clips in clips.json, with transcripts, with image analysis."""
    a = analysis_dir(paths)
    clips = clips_json(paths)
    return {
        "clips": len(clips),
        "transcribed": sum(1 for c in clips if (a / "transcripts" / f"{c['id']}.json").is_file()),
        "analysed": sum(1 for c in clips if (a / "image" / f"{c['id']}.json").is_file()),
        "fingerprints": {fingerprint_key(c) for c in clips if c.get("original")},
    }


def fingerprint_key(clip: dict) -> str:
    fp = clip["original"]["fingerprint"]
    return f"{fp['size']}:{fp['head_sha256']}:{fp['tail_sha256']}"
