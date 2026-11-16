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
    write_atomic(paths.data / "briefing.md", render(data, t, project_name).encode())


def render(data: dict, t, project_name: str) -> str:
    lines = [f"# {t('briefing.md.title', name=project_name)}", "", t("briefing.md.note"), ""]
    for key in FIELDS:
        value = data.get(key)
        if not value:
            continue
        if key == "music":
            value = t(f"briefing.music.{value}")
        lines += [f"## {t('briefing.' + key)}", "", value, ""]
    missing = [t("briefing." + k) for k in FIELDS if not data.get(k)]
    if missing:
        lines += [f"## {t('briefing.md.missing')}", "", ", ".join(missing), ""]
    return "\n".join(lines)
