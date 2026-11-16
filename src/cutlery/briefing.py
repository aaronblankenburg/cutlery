"""The briefing: what the user wants from the film, stored as briefing.json and written as briefing.md.

briefing.md in the project folder switches cutter's video-editing skill into its non-interactive mode
(cutter: skills/video-editing/references/non-interactive.md). The texts stay in the language the user wrote them in;
the headings are in the UI language.
"""
from __future__ import annotations

import json

from .storage import ProjectPaths, write_atomic

FIELDS = ("prompt", "purpose", "length", "style", "music", "nogos")
MUSIC = ("own_only", "own_and_search", "search", "none")
MAX = 4000


class BriefingError(ValueError):
    pass


def check(data: dict) -> dict:
    if not isinstance(data, dict):
        raise BriefingError("briefing must be an object")
    out = {}
    for k in FIELDS:
        v = data.get(k, "")
        if not isinstance(v, str) or len(v) > MAX:
            raise BriefingError(f"{k} must be a text of at most {MAX} characters")
        out[k] = v.strip()
    if out["music"] and out["music"] not in MUSIC:
        raise BriefingError(f"music must be one of {', '.join(MUSIC)}")
    if not out["prompt"]:
        raise BriefingError("the description of the film is missing")
    return out


def load(paths: ProjectPaths) -> dict:
    try:
        return json.loads((paths.data / "briefing.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(paths: ProjectPaths, data: dict, t, project_name: str) -> None:
    write_atomic(paths.data / "briefing.json", json.dumps(data, ensure_ascii=False, indent=1).encode())
    refresh(paths, t, project_name)


def refresh(paths: ProjectPaths, t, project_name: str) -> None:
    """Write briefing.md again (after the briefing or the files changed). Without a saved briefing there is none."""
    from . import files

    data = load(paths)
    if data:
        write_atomic(paths.data / "briefing.md", render(data, t, project_name, files.listing(paths)).encode())


def render(data: dict, t, project_name: str, files: dict | None = None) -> str:
    lines = [f"# {t('briefing.md.title', name=project_name)}", "", t("briefing.md.note"), ""]
    for key in FIELDS:
        value = data.get(key)
        if not value:
            continue
        if key == "music":
            value = t(f"briefing.music.{value}")
        lines += [f"## {t('briefing.' + key)}", "", value, ""]
    files = files or {}
    if files.get("storyboard"):
        lines += [f"## {t('files.storyboard')}", "", *[f"- storyboard/{f['name']}" for f in files["storyboard"]], ""]
    if files.get("images"):
        lines += [f"## {t('files.images')}", "", *[f"- images/{f['name']}" for f in files["images"]], ""]
    if files.get("music"):
        lines += [f"## {t('files.music')}", "", t("briefing.md.music_note"), ""]
        for f in files["music"]:
            lic = f.get("license") or "other"
            use = t("briefing.md.music_use") if lic != "other" else t("briefing.md.music_skip")
            lines.append(f"- music/{f['name']}: {t('files.license.' + lic.replace(' ', '_').lower())} – {use}")
        lines.append("")
    missing = [t("briefing." + k) for k in FIELDS if not data.get(k)]
    if missing:
        lines += [f"## {t('briefing.md.missing')}", "", ", ".join(missing), ""]
    return "\n".join(lines)
