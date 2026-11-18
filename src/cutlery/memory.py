"""What Claude keeps between the cuts of a project, and the user's preferences for all projects.

- Project memory: CLAUDE.md in the project folder. Claude Code reads it at the start of every session (the agent
  runs in that folder); the prompt asks the agent to keep it up to date before it finishes. The user can read and
  change it on the status page.
- Preferences: DATA/preferences.md, written by the user on the settings page, given to every job in its prompt
  (the job container sees only its own project, so the text goes into the prompt, not a file).
"""
from __future__ import annotations

import pathlib

from .storage import write_atomic

MAX_MEMORY = 20_000          # characters
MAX_PREFERENCES = 4_000
MAX_NOTE = 4_000

MEMORY_START = """# Project memory

Kept by Claude between the cuts of this project and readable/editable by the user in Cutlery. Short notes only:
what the user likes and dislikes, what was tried and rejected, who is who, open points. Facts that are in the
briefing, the analysis or the cut files are not repeated here.
"""


def memory_file(paths) -> pathlib.Path:
    return paths.data / "CLAUDE.md"


def read_memory(paths) -> str:
    try:
        return memory_file(paths).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_memory(paths, text: str) -> None:
    text = _clean(text, MAX_MEMORY, "memory")
    write_atomic(memory_file(paths), text.encode("utf-8"))


def ensure_memory(paths) -> None:
    """Start the project memory before the first job, so the agent finds the file and its purpose."""
    f = memory_file(paths)
    if not f.exists():
        write_atomic(f, MEMORY_START.encode("utf-8"))


def preferences_file(cfg) -> pathlib.Path:
    return cfg.data_dir / "preferences.md"


def read_preferences(cfg) -> str:
    try:
        return preferences_file(cfg).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_preferences(cfg, text: str) -> None:
    write_atomic(preferences_file(cfg), _clean(text, MAX_PREFERENCES, "preferences").encode("utf-8"))


def _clean(text, limit, what) -> str:
    if not isinstance(text, str):
        raise ValueError(f"{what}: text expected")
    text = text.replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise ValueError(f"{what}: at most {limit} characters")
    return text + "\n" if text else ""
