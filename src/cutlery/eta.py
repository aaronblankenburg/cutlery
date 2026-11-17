"""How long the analysis of a project still needs, measured on this machine.

`cutter analyze` works in stages, each over all new clips: contact sheets, transcripts (clips with sound), image
analysis. Each stage leaves one file per clip (sheets/<id>.jpg, transcripts/<id>.json, image/<id>.json), so the
modification times say how many wall-clock seconds a stage needs per second of footage. That cost is learnt from the
running analysis (or, before a stage has produced three files in this run, from earlier runs of any project, kept
in WORK/analysis-rates.json) and multiplied by the footage still missing in each stage.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import time

from . import cutter_project
from .storage import write_atomic

STAGES = (
    ("sheets", "sheets/{id}.jpg", lambda c: True),
    ("transcripts", "transcripts/{id}.json", lambda c: c.get("has_audio", True)),
    ("image", "image/{id}.json", lambda c: True),
)
MAX_GAP = 15 * 60        # earlier runs: a longer pause between two files is not work


def _cost(samples: list[tuple[float, float]], max_gap: float | None) -> float | None:
    """Wall seconds per second of footage from (mtime, duration) of finished clips, or None with too little data."""
    samples = sorted(samples)
    wall = footage = 0.0
    for (prev, _), (t, d) in zip(samples, samples[1:]):
        gap = t - prev
        if max_gap is not None and gap > max_gap:
            continue
        wall += gap
        footage += d
    if footage <= 0 or wall < 20:
        return None
    return wall / footage


def estimate(paths, since: float, rates_file: pathlib.Path) -> dict:
    """{"seconds": remaining wall-clock seconds or None if a stage's speed is unknown yet, "stages": {...}}.

    since: when the running analysis started (epoch seconds); files written since then measure the current speed."""
    clips = cutter_project.clips_json(paths)
    a = cutter_project.analysis_dir(paths)
    try:
        rates = json.loads(rates_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rates = {}
    learnt = {}
    total, unknown, stages = 0.0, False, {}
    for stage, pattern, needed in STAGES:
        now, before, missing = [], [], 0.0
        for c in clips:
            if not needed(c):
                continue
            d = float(c.get("duration") or 0)
            try:
                m = (a / pattern.format(id=c["id"])).stat().st_mtime
            except OSError:
                missing += d
                continue
            (now if m >= since else before).append((m, d))
        cost = _cost(now, None) if len(now) >= 3 else None
        if cost is not None:
            learnt[stage] = cost
        else:
            cost = rates.get(stage) or _cost(before, MAX_GAP)
        stages[stage] = {"missing_s": round(missing, 1), "cost": cost}
        if missing:
            if cost is None:
                unknown = True
            else:
                total += missing * cost
    if learnt and learnt != {k: rates.get(k) for k in learnt}:
        try:
            write_atomic(rates_file, json.dumps({**rates, **learnt}, indent=1).encode())
        except OSError:
            pass
    return {"seconds": None if unknown else round(total), "stages": stages}


def remaining(project: dict, paths, work_dir: pathlib.Path) -> int | None:
    """Seconds the project's analysis still needs, or None (not running, or the speed is not known yet)."""
    if project["analysis_state"] not in ("running", "waiting"):
        return None
    since = (datetime.datetime.fromisoformat(project["analysis_updated"]).timestamp()
             if project["analysis_updated"] else time.time())
    return estimate(paths, since, work_dir / "analysis-rates.json")["seconds"]
