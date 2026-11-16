"""Files the user adds to a project: storyboard, images and music (with a licence statement per track).

They live in the project folder in the data tree (storyboard/, images/, music/), where the agent finds them; music
is where cutter expects it. The licence of each track is stored in music/licenses.json and written into briefing.md;
a track whose licence is "other / unclear" is not used.
"""
from __future__ import annotations

import json
import pathlib
import re
import unicodedata

from .storage import write_atomic

KINDS = {
    "storyboard": {"ext": {".pdf", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp", ".heic"}, "max": 200 * 1024 ** 2},
    "images": {"ext": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".svg"}, "max": 200 * 1024 ** 2},
    "music": {"ext": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif"}, "max": 1024 ** 3},
}
LICENSES = ("own", "CC BY 4.0", "CC0", "other")
USABLE = {"own", "CC BY 4.0", "CC0"}


class FileError(ValueError):
    pass


def safe_name(name: str) -> str:
    """A file name without folders or control characters (letters of any language stay)."""
    name = unicodedata.normalize("NFC", pathlib.PurePosixPath(str(name).replace("\\", "/")).name)
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C").strip().lstrip(".")
    name = re.sub(r"\s+", " ", name)
    if not name or name in (".", "..") or len(name.encode()) > 200:
        raise FileError("invalid file name")
    return name


def folder(paths, kind) -> pathlib.Path:
    if kind not in KINDS:
        raise FileError("unknown kind")
    return paths.data / kind


def check(kind: str, name: str, size: int) -> str:
    name = safe_name(name)
    spec = KINDS.get(kind)
    if spec is None:
        raise FileError("unknown kind")
    if pathlib.PurePosixPath(name).suffix.lower() not in spec["ext"]:
        raise FileError("file type not accepted here")
    if size > spec["max"]:
        raise FileError("file too large")
    return name


def licenses(paths) -> dict:
    try:
        return json.loads((paths.data / "music" / "licenses.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_license(paths, name: str, license: str) -> None:
    if license not in LICENSES:
        raise FileError("unknown licence")
    name = safe_name(name)
    if not (paths.data / "music" / name).is_file():
        raise FileError("no such track")
    lic = licenses(paths)
    lic[name] = license
    write_atomic(paths.data / "music" / "licenses.json", json.dumps(lic, ensure_ascii=False, indent=1).encode())


def listing(paths) -> dict:
    lic = licenses(paths)
    out = {}
    for kind in KINDS:
        d = folder(paths, kind)
        files = sorted(f for f in d.iterdir() if f.is_file() and pathlib.PurePosixPath(f.name).suffix.lower() in KINDS[kind]["ext"]) \
            if d.is_dir() else []
        out[kind] = [{"name": f.name, "size": f.stat().st_size, **({"license": lic.get(f.name)} if kind == "music" else {})}
                     for f in files]
    return out


def remove(paths, kind, name) -> None:
    name = safe_name(name)
    (folder(paths, kind) / name).unlink(missing_ok=True)
    if kind == "music":
        lic = licenses(paths)
        if lic.pop(name, None) is not None:
            write_atomic(paths.data / "music" / "licenses.json", json.dumps(lic, ensure_ascii=False, indent=1).encode())
