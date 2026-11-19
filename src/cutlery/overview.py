"""Where a project stands: for the project list, the status page and the readiness checklist."""
from __future__ import annotations

import json

from . import briefing, cutter_project, files


def ready(projects, worker, project_id) -> str | None:
    """None if a Claude job of the project may start now, else what it waits for: "upload" or "analysis"."""
    clips = projects.clips(project_id)
    if not any(c["state"] == "uploaded" for c in clips) or any(c["state"] == "registered" for c in clips):
        return "upload"
    p = projects.get(project_id)
    if p["analysis_state"] in ("waiting", "running") or worker.pending(project_id):
        return "analysis"
    if not cutter_project.clips_json(projects.paths(project_id)):
        return "analysis"
    return None


def analysis_facts(projects, project_id: str) -> dict:
    """Numbers for the status page: clips analysed (of the uploaded ones), speech, usable time, clips with a place.

    Read from what is there now, also during a run: cutter lists a clip in clips.json as soon as it has read its
    metadata and writes speech_seconds only at the end of a run, so the per-clip files count (image/<id>.json:
    analysed; transcripts/<id>.json: speech, like cutter without suspect segments). Places come from the sidecars."""
    paths = projects.paths(project_id)
    ours = [c for c in projects.clips(project_id) if c["state"] == "uploaded"]
    known = {c["rel_path"] for c in ours}
    clips = [c for c in cutter_project.clips_json(paths) if c.get("rel_path") in known]
    a = cutter_project.analysis_dir(paths)
    usable = speech = 0.0
    analysed = 0
    for c in clips:
        try:
            img = json.loads((a / "image" / f"{c['id']}.json").read_text(encoding="utf-8"))
            analysed += 1
            usable += (img.get("summary") or {}).get("usable_s") or 0
        except (OSError, ValueError):
            pass
        try:
            tr = json.loads((a / "transcripts" / f"{c['id']}.json").read_text(encoding="utf-8"))
            speech += sum(s["end"] - s["start"] for s in tr.get("segments", []) if not s.get("suspect"))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    gps = 0
    for c in ours:
        try:
            sc = projects.sidecar(project_id, c)
        except (OSError, ValueError):
            continue
        tel = sc.get("telemetry") or {}
        gps += bool(sc.get("gps") or tel.get("gpmd") or tel.get("srt"))
    return {"clips": analysed, "uploaded": len(ours), "speech_s": round(speech, 1), "usable_s": usable, "gps": gps,
            "duration_s": sum(c.get("duration") or 0 for c in clips)}


def people(paths) -> list[dict]:
    """The people found by their faces, without the face vectors (those never leave the server)."""
    try:
        data = json.loads((cutter_project.analysis_dir(paths) / "people.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"id": p["id"], "name": p.get("name") or "", "seconds": p.get("seconds") or 0,
             "clips": sorted(p.get("clips") or {}), "pictures": [str(x).rsplit("/", 1)[-1] for x in p.get("pictures", [])][:4]}
            for p in data.get("people", [])]


def state(projects, worker, jobs, reviews, project) -> dict:
    """Everything the pages show about a project."""
    pid = project["id"]
    paths = projects.paths(pid)
    clips = projects.clips(pid)
    usable = [c for c in clips if c["state"] != "unsupported"]
    uploaded = [c for c in usable if c["state"] == "uploaded"]
    progress = cutter_project.progress(paths)
    job = jobs.for_project(pid)[:1]
    job = job[0] if job else None
    cuts = reviews.summary(pid)
    fl = files.listing(paths)
    bf = briefing.load(paths)
    return {
        "clips": len(clips), "usable": len(usable), "uploaded": len(uploaded),
        "unsupported": len(clips) - len(usable), "failed": sum(1 for c in clips if c["state"] == "failed"),
        "duration_s": sum(c["duration"] or 0 for c in usable),
        "analysis": {"state": project["analysis_state"], "message": project["analysis_message"],
                     **{k: v for k, v in progress.items() if k != "fingerprints"}},
        "briefing": bf, "briefing_saved": bool(bf), "files": fl, "frame": cutter_project.frame(paths),
        "analysis_parts": cutter_project.analysis_parts(paths),
        "music_unclear": sum(1 for f in fl["music"] if (f.get("license") or "other") == "other"),
        "job": job, "cuts": cuts, "ready": ready(projects, worker, pid),
        "open_comments": sum(c.get("open", 0) for c in cuts if not c.get("locked")),
    }


def badge(st) -> tuple[str, str]:
    """(translation key, style) of the project's status badge."""
    job = st["job"]
    if job and job["state"] == "paused":
        return "status.paused", "error"
    if job and job["state"] in ("queued", "running"):
        return ("status.cutting", "warn") if job["state"] == "running" else ("status.queued", "warn")
    if st["cuts"]:
        if st["open_comments"]:
            return "status.review_open", "info"
        return "status.review", "info"
    if st["uploaded"] < st["usable"]:
        return "status.uploading", "warn"
    if st["analysis"]["state"] in ("waiting", "running"):
        return "status.analysing", "warn"
    if st["usable"]:
        return "status.ready", "ok"
    return "status.new", ""
