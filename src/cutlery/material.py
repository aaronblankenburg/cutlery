"""The material page: the footage by day for the user's own viewing, and the user's selection (selects.json).

Everything comes from what the analysis left behind (cutter's clips.json, transcripts/, image/, people.json) and from
the cuts (which shots use a clip). Only the clips Cutlery knows (same rel_path) are shown, as on the analysis page.

The selection is the user's own: favourites ("must go in, if it fits"), rejects ("leave out") and tags (the user's
own labels, "border crossing"), each for a whole clip or a range, with an optional note. It is stored in the project folder as selects.json, in the format cutter's
video-editing skill reads (cutter docs/formats.md, "selects.json"), and summarised in briefing.md:

    {"version": 1, "selects": [{"id": "s3", "clip": "C005", "file": "2012-07-14 09-26-28 DSC_0669.MOV",
                                "kind": "favorite" | "reject" | "tag", "tag": "…" (kind tag only),
                                "in": 3.2, "out": 8.0, "note": "", "created": "…"}]}

What the analysis found in the picture comes along as tags too, read-only: content tags (CLIP: desert, road, campfire
…), objects (YOLOX: car, dog …) and camera moves, each with the seconds it covers; detail() gives their time ranges.

"in"/"out" are seconds in the clip; both null: the whole clip.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import threading

from . import cutter_project
from .storage import ProjectPaths, write_atomic

KINDS = ("favorite", "reject", "tag")
NOTE_MAX = 500
TAG_MAX = 40
MIN_S = 2.0                                  # an automatic tag counts for a clip from this many seconds on
MOVES = ("pan_left", "pan_right", "tilt_up", "tilt_down", "push_in", "pull_out", "static", "handheld")
BATCH_MAX = 500
CUT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_lock = threading.Lock()


class SelectError(ValueError):
    pass


# ---------------------------------------------------------------- the footage

def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _day(created: str | None) -> str | None:
    """"2012-07-14 09:26:28" (local time of the camera) -> "2012-07-14"; None when there is no usable date."""
    if not created or not re.match(r"^\d{4}-\d{2}-\d{2}", str(created)):
        return None
    return str(created)[:10]


def _usage(paths: ProjectPaths) -> dict[str, list[dict]]:
    """{clip id: [{"cut", "nr", "in", "out", "cutaway"}]} from the edit lists of all cuts, newest cut first."""
    out: dict[str, list[dict]] = {}
    cuts_dir = paths.data / "cuts"
    if not cuts_dir.is_dir():
        return out

    def num(name):
        m = re.search(r"(\d+)$", name)
        return int(m.group(1)) if m else 0
    for d in sorted((x for x in cuts_dir.iterdir() if x.is_dir() and CUT_RE.match(x.name)), key=lambda x: -num(x.name)):
        edit = _read(d / "edit.json")
        if not isinstance(edit, dict):
            continue
        for sec in edit.get("sections") or []:
            for s in sec.get("shots") or []:
                if s.get("clip"):
                    out.setdefault(s["clip"], []).append({"cut": d.name, "nr": s.get("nr"), "in": s.get("in"),
                                                          "out": s.get("out"), "cutaway": False})
                for c in s.get("cutaways") or []:
                    if c.get("clip"):
                        out.setdefault(c["clip"], []).append({"cut": d.name, "nr": s.get("nr"), "in": c.get("in"),
                                                              "out": c.get("out"), "cutaway": True})
    return out


def footage(paths: ProjectPaths, known_rel_paths: set[str]) -> dict:
    """The data of the material page: clips sorted by recording time, the days, people, cuts, the selection."""
    a = cutter_project.analysis_dir(paths)
    clips = [c for c in cutter_project.clips_json(paths) if c.get("rel_path") in known_rel_paths]
    people_data = _read(a / "people.json") or {}
    people = []
    in_clip: dict[str, list[str]] = {}
    for p in people_data.get("people") or []:
        name = p.get("name") or p.get("id")
        people.append({"id": p.get("id"), "name": name, "seconds": p.get("seconds", 0)})
        for cid in (p.get("clips") or {}):
            in_clip.setdefault(cid, []).append(p.get("id"))
    usage = _usage(paths)
    rows = []
    for c in clips:
        cid = c["id"]
        img = _read(a / "image" / f"{cid}.json") or {}
        summary = img.get("summary") or {}
        tr = _read(a / "transcripts" / f"{cid}.json") or {}
        segs = [s for s in tr.get("segments") or [] if not s.get("suspect")]
        words = sum(len(s.get("words") or []) or len(str(s.get("text", "")).split()) for s in segs)
        w, h = c.get("width") or 16, c.get("height") or 9
        if (c.get("rotation") or 0) % 180:
            w, h = h, w
        original = c.get("original") or {}
        rows.append({
            "id": cid, "file": c.get("file") or original.get("name"), "rel_path": c.get("rel_path"),
            "created": c.get("created"), "created_source": c.get("created_source"), "day": _day(c.get("created")),
            "duration": c.get("duration") or 0, "width": w, "height": h, "has_audio": c.get("has_audio", True),
            "camera": camera(c), "words": words, "text": " ".join(str(s.get("text", "")).strip() for s in segs)[:4000],
            "language": tr.get("language"), "people": in_clip.get(cid, []),
            "usable": [[u.get("start"), u.get("end"), bool(u.get("steady"))] for u in img.get("usable") or []],
            "flaws": [[f.get("start"), f.get("end"), f.get("why") or []] for f in img.get("flaws") or []],
            "speech_s": summary.get("speech_s"), "strip": (a / "strips" / f"{cid}.jpg").is_file(),
            "used": usage.get(cid, []), **auto_tags(img, c.get("duration")),
        })
    rows.sort(key=lambda r: (r["day"] is None, r["created"] or "", r["file"] or "", r["id"]))
    days = []
    for r in rows:
        if not days or days[-1]["day"] != r["day"]:
            days.append({"day": r["day"], "clips": 0, "seconds": 0.0})
        days[-1]["clips"] += 1
        days[-1]["seconds"] += float(r["duration"] or 0)
    cuts = sorted({u["cut"] for us in usage.values() for u in us}, key=lambda n: int(re.sub(r"\D", "", n) or 0))
    hide = load_hidden(paths)
    for r in rows:
        gone = set(hide["all"]) | set(hide["clips"].get(r["id"], []))
        for kind, field in KIND_FIELDS.items():
            r[field] = [x for x in r[field] if f"{kind}:{x[0]}" not in gone]
    return {"clips": rows, "days": days, "people": people, "cuts": cuts, "selects": load(paths)["selects"],
            "hidden": hide, "suggestions": suggestions(paths)}


def _seconds(rs) -> float:
    return round(sum(r["end"] - r["start"] for r in rs), 1)


def auto_ranges(img: dict, duration=None) -> dict:
    """{"tags": {name: [ranges]}, "objects": {…}, "moves": {…}}: where the analysis found what, in seconds."""
    from cutter import content_tags, quality
    per = img.get("per_second") or []
    times = [p.get("t", i) for i, p in enumerate(per)]
    objects = {}
    for name in sorted({k for p in per for k in (p.get("objects") or {})}):
        rs = quality.ranges(times, [name in (p.get("objects") or {}) for p in per], min_len=1.0, close_gap=1.0,
                            end=duration)
        if rs:
            objects[name] = rs
    moves = {}
    for seg in img.get("camera") or []:
        if seg.get("move") in MOVES and seg["end"] - seg["start"] >= 1.0:
            moves.setdefault(seg["move"], []).append({"start": seg["start"], "end": seg["end"]})
    return {"tags": content_tags.ranges(per, duration) if img.get("tags_key") else {}, "objects": objects,
            "moves": moves}


def auto_tags(img: dict, duration=None) -> dict:
    """For a tile and the filter: [[name, seconds], …] per kind, the longest first, from MIN_S seconds on."""
    out = {}
    for kind, found in auto_ranges(img, duration).items():
        items = [[name, _seconds(rs)] for name, rs in found.items()]
        out[kind] = sorted([x for x in items if x[1] >= MIN_S], key=lambda x: -x[1])
    return out


def detail(paths: ProjectPaths, cid: str) -> dict:
    """The time ranges of a clip's automatic tags, for the source monitor: {kind: {name: [[a, b], …]}}, without the
    ones the user hid."""
    if not re.match(r"^[A-Za-z0-9_-]{1,20}$", cid):
        return {"tags": {}, "objects": {}, "moves": {}}
    img = _read(cutter_project.analysis_dir(paths) / "image" / f"{cid}.json") or {}
    hide = load_hidden(paths)
    gone = set(hide["all"]) | set(hide["clips"].get(cid, []))
    short = {v: k for k, v in KIND_FIELDS.items()}
    return {kind: {name: [[r["start"], r["end"]] for r in rs] for name, rs in found.items()
                   if f"{short[kind]}:{name}" not in gone}
            for kind, found in auto_ranges(img, img.get("duration")).items()}


# ---------------------------------------------------------------- managing tags

KIND_FIELDS = {"c": "tags", "o": "objects", "m": "moves"}
KEY_RE = re.compile(r"^[com]:[A-Za-z0-9 _-]{1,40}$")
CLIP_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def _hidden_file(paths: ProjectPaths):
    return paths.data / "tags_hidden.json"


def load_hidden(paths: ProjectPaths) -> dict:
    """Automatic tags the user marked as wrong: {"all": ["c:snow"], "clips": {"C017": ["o:dog"]}}."""
    d = _read(_hidden_file(paths))
    if not isinstance(d, dict):
        return {"all": [], "clips": {}}
    return {"all": [x for x in d.get("all") or [] if isinstance(x, str)],
            "clips": {k: [x for x in v if isinstance(x, str)] for k, v in (d.get("clips") or {}).items()
                      if isinstance(v, list)}}


def hide(paths: ProjectPaths, key: str, clip: str | None = None, hidden: bool = True) -> dict:
    """Hide an automatic tag (key "c:snow", "o:dog", "m:pan_left") for one clip or everywhere, or show it again."""
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise SelectError("key: c:, o: or m: and the tag's name")
    if clip is not None and (not isinstance(clip, str) or not CLIP_RE.match(clip)):
        raise SelectError("unknown clip")
    with _lock:
        d = load_hidden(paths)
        items = d["all"] if clip is None else d["clips"].setdefault(clip, [])
        if hidden and key not in items:
            items.append(key)
        if not hidden:
            items[:] = [x for x in items if x != key]
            if clip is None:                      # shown again everywhere: also where it was hidden per clip
                for v in d["clips"].values():
                    v[:] = [x for x in v if x != key]
        d["clips"] = {k: v for k, v in d["clips"].items() if v}
        write_atomic(_hidden_file(paths), (json.dumps(d, ensure_ascii=False, indent=1) + "\n").encode())
        return d


def remove_tag(paths: ProjectPaths, tag: str, clips=None) -> int:
    """Delete one of the user's tags, everywhere or from the given clips. Returns how many entries went."""
    if not isinstance(tag, str) or not tag.strip():
        raise SelectError("tag: a name")
    if clips is not None and (not isinstance(clips, list) or not all(isinstance(c, str) for c in clips)):
        raise SelectError("clips: a list of clip IDs")
    with _lock:
        d = load(paths)
        keep = [x for x in d["selects"] if not (x.get("kind") == "tag" and x.get("tag") == tag
                                                and (clips is None or x.get("clip") in clips))]
        n = len(d["selects"]) - len(keep)
        if n:
            d["selects"] = keep
            _save(paths, d)
        return n


def rename_tag(paths: ProjectPaths, old: str, new: str) -> int:
    """Rename one of the user's tags everywhere (two tags become one when the new name is taken)."""
    new = " ".join(str(new or "").split())
    if not new or len(new) > TAG_MAX:
        raise SelectError(f"tag: a name of 1 to {TAG_MAX} characters")
    with _lock:
        d = load(paths)
        n = 0
        for x in d["selects"]:
            if x.get("kind") == "tag" and x.get("tag") == old:
                x["tag"], n = new, n + 1
        seen, keep = set(), []
        for x in d["selects"]:                    # the same tag on the same range twice: once
            k = (x.get("clip"), x.get("kind"), x.get("tag"), x.get("in"), x.get("out")) if x.get("kind") == "tag" else id(x)
            if k not in seen:
                seen.add(k)
                keep.append(x)
        d["selects"] = keep
        if n:
            _save(paths, d)
        return n


def camera(c: dict) -> str:
    """A name for the camera a clip came from: the device from the metadata, else the kind of file name
    (DSC_…, GOPR…, 000…), the codec and the picture size."""
    if c.get("device"):
        return str(c["device"])
    name = (c.get("original") or {}).get("name") or c.get("file") or ""
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[ _T]\d{2}[-.:]\d{2}[-.:]\d{2}\s*", "", name.rsplit(".", 1)[0])
    prefix = re.match(r"[A-Za-z_]+", stem)
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
    label = (prefix.group(0).rstrip("_") if prefix else "") or ext or "?"
    short = min(c.get("width") or 0, c.get("height") or 0) or c.get("height")
    return f"{label} {short}p" if short else label


POSTER_W = 640                               # the stand-ins are 360p: their own size, sharp up to the largest tile
_posters = threading.BoundedSemaphore(3)


def poster(paths: ProjectPaths, cid: str):
    """A still from the middle of the clip for its tile, made once from the stand-in (WORK/posters/<ID>.jpg) and
    made again when the stand-in changes. None when the clip or its stand-in is unknown."""
    if not re.match(r"^[A-Za-z0-9_-]{1,20}$", cid):
        return None
    clip = next((c for c in cutter_project.clips_json(paths) if c.get("id") == cid), None)
    if not clip or not clip.get("rel_path"):
        return None
    src = paths.standin(clip["rel_path"]).resolve()
    if not src.is_relative_to(paths.media.resolve()) or not src.is_file():
        return None
    target = paths.work / "posters" / f"{cid}.jpg"
    try:
        if target.stat().st_mtime >= src.stat().st_mtime:
            return target
    except OSError:
        pass
    with _posters:                               # a page of tiles asks for many at once
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{cid}.part.jpg")
        at = max(0.0, float(clip.get("duration") or 0) / 2)
        for seek in (at, 0.0):                   # the middle; a clip whose middle cannot be read: its start
            r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{seek:.2f}", "-i", str(src),
                                "-frames:v", "1", "-vf", f"scale='min({POSTER_W},iw)':-2", "-q:v", "3", str(tmp)],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size:
                os.replace(tmp, target)
                return target
        tmp.unlink(missing_ok=True)
    return None


def transcript(paths: ProjectPaths, cid: str) -> dict:
    """A clip's transcript for the player: segments with words [start, end, text]."""
    if not re.match(r"^[A-Za-z0-9_-]{1,20}$", cid):
        return {"segments": []}
    tr = _read(cutter_project.analysis_dir(paths) / "transcripts" / f"{cid}.json") or {}
    segs = []
    for s in tr.get("segments") or []:
        segs.append({"start": s.get("start"), "end": s.get("end"), "text": str(s.get("text", "")).strip(),
                     "unsure": bool(s.get("suspect")) or (s.get("avg_logprob", 0) or 0) < -1.0,
                     "words": [[w[0], w[1], w[2]] for w in s.get("words") or [] if len(w) >= 3]})
    return {"language": tr.get("language"), "segments": segs}


# ---------------------------------------------------------------- the selection

# A guest's favourites, rejects and tags (share.py) are suggestions: the same entries with "by" (the guest's name) in
# suggestions.json, which Claude does not read; the owner takes them over into selects.json (accept) or not.

def _file(paths: ProjectPaths, by: str | None = None):
    return paths.data / ("suggestions.json" if by else "selects.json")


def load(paths: ProjectPaths, by: str | None = None) -> dict:
    """selects.json; with `by` the suggestions (all of them: filter on "by" for one guest's)."""
    d = _read(_file(paths, by))
    if not isinstance(d, dict) or not isinstance(d.get("selects"), list):
        return {"version": 1, "selects": []}
    return d


def _save(paths: ProjectPaths, d: dict, by: str | None = None) -> None:
    write_atomic(_file(paths, by), (json.dumps(d, ensure_ascii=False, indent=1) + "\n").encode())


def suggestions(paths: ProjectPaths, by: str | None = None) -> list[dict]:
    """The guests' suggestions (one guest's with `by`)."""
    return [x for x in load(paths, by="*")["selects"] if by is None or x.get("by") == by]


def decide(paths: ProjectPaths, ids, accept: bool) -> dict:
    """Take suggestions over into the selection (accept) or drop them. Returns {"done", "selects", "suggestions"}."""
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise SelectError("ids: a list of suggestion IDs")
    with _lock:
        sug = load(paths, by="*")
        chosen = [x for x in sug["selects"] if x.get("id") in ids]
        sug["selects"] = [x for x in sug["selects"] if x.get("id") not in ids]
        d = load(paths)
        if accept and chosen:
            n = max([int(x["id"][1:]) for x in d["selects"] if re.match(r"^s\d+$", str(x.get("id")))] or [0])
            same = lambda a, b: all(a.get(k) == b.get(k) for k in ("clip", "kind", "tag", "in", "out"))
            for x in chosen:
                if any(same(x, y) for y in d["selects"]):
                    continue
                n += 1
                entry = {k: v for k, v in x.items() if k not in ("id", "by")}
                if x.get("by"):
                    note = entry.get("note") or ""
                    entry["note"] = f"{note} ({x['by']})".strip() if note else f"({x['by']})"
                d["selects"].append({"id": f"s{n}", **entry})
            _save(paths, d)
        _save(paths, sug, by="*")
        return {"done": len(chosen), "selects": d["selects"], "suggestions": sug["selects"]}


def _check(body: dict, clips: dict[str, dict], old: dict | None = None) -> dict:
    s = dict(old or {})
    if old is None or "clip" in body:
        cid = body.get("clip")
        if cid not in clips:
            raise SelectError("unknown clip")
        s["clip"], s["file"] = cid, clips[cid].get("file")
    if old is None or "kind" in body:
        if body.get("kind") not in KINDS:
            raise SelectError("kind must be favorite, reject or tag")
        s["kind"] = body["kind"]
    if s["kind"] == "tag":
        if "tag" in body or old is None or "tag" not in s:
            tag = " ".join(str(body.get("tag") or "").split())
            if not tag or len(tag) > TAG_MAX:
                raise SelectError(f"tag: a name of 1 to {TAG_MAX} characters")
            s["tag"] = tag
    else:
        s.pop("tag", None)
    if "note" in body or old is None:
        note = body.get("note") or ""
        if not isinstance(note, str) or len(note) > NOTE_MAX:
            raise SelectError(f"note: text of at most {NOTE_MAX} characters")
        s["note"] = " ".join(note.split())
    if "in" in body or "out" in body or old is None:
        a, b = body.get("in", s.get("in")), body.get("out", s.get("out"))
        if a is None and b is None:
            s["in"] = s["out"] = None
        else:
            try:
                a, b = round(float(a), 2), round(float(b), 2)
            except (TypeError, ValueError):
                raise SelectError("in and out: seconds in the clip (or both empty for the whole clip)")
            dur = float(clips[s["clip"]].get("duration") or 0)
            if not (0 <= a < b) or (dur and b > dur + 0.5):
                raise SelectError("in and out: 0 <= in < out <= the clip's duration")
            s["in"], s["out"] = a, b
    return s


def add(paths: ProjectPaths, body: dict, clips: dict[str, dict], by: str | None = None) -> dict:
    return add_many(paths, [body], clips, by)[0]


def add_many(paths: ProjectPaths, bodies: list[dict], clips: dict[str, dict], by: str | None = None) -> list[dict]:
    """Several entries at once (one tag for the clips chosen on the page); all or none. The same tag on the same
    range of a clip is not added twice. With `by` (a guest) they go into the suggestions."""
    if not isinstance(bodies, list) or not 0 < len(bodies) <= BATCH_MAX:
        raise SelectError(f"1 to {BATCH_MAX} entries")
    prefix = "g" if by else "s"
    with _lock:
        d = load(paths, by)
        checked = [_check(b if isinstance(b, dict) else {}, clips) for b in bodies]
        n = max([int(x["id"][1:]) for x in d["selects"] if re.match(rf"^{prefix}\d+$", str(x.get("id")))] or [0])
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        same = lambda a, b: all(a.get(k) == b.get(k) for k in ("clip", "kind", "tag", "in", "out", "by"))
        out = []
        for s in checked:
            if by:
                s["by"] = by
            old = next((x for x in d["selects"] if s["kind"] == "tag" and same(x, s)), None)
            if old:
                out.append(old)
                continue
            n += 1
            s = {"id": f"{prefix}{n}", **s, "created": stamp}
            d["selects"].append(s)
            out.append(s)
        _save(paths, d, by)
        return out


def change(paths: ProjectPaths, sid: str, body: dict, clips: dict[str, dict], by: str | None = None) -> dict | None:
    """Change an entry; a guest only their own suggestions."""
    with _lock:
        d = load(paths, by)
        for i, x in enumerate(d["selects"]):
            if x.get("id") == sid and (by is None or x.get("by") == by):
                d["selects"][i] = _check({k: v for k, v in body.items() if k != "by"}, clips, x)
                _save(paths, d, by)
                return d["selects"][i]
        return None


def remove(paths: ProjectPaths, sid: str, by: str | None = None) -> bool:
    with _lock:
        d = load(paths, by)
        keep = [x for x in d["selects"] if not (x.get("id") == sid and (by is None or x.get("by") == by))]
        if len(keep) == len(d["selects"]):
            return False
        d["selects"] = keep
        _save(paths, d, by)
        return True


def briefing_lines(paths: ProjectPaths, t) -> list[str]:
    """The selection as a section of briefing.md (empty when there is none)."""
    sel = load(paths)["selects"]
    wrong_any = load_hidden(paths)
    if not sel and not (wrong_any["all"] or wrong_any["clips"]):
        return []

    def when(x):
        if x.get("in") is None:
            return t("material.md.whole")
        return t("material.md.range", a=_mmss(x["in"]), b=_mmss(x["out"]))
    lines = [f"## {t('material.md.title')}", "", t("material.md.note"), ""]
    order = lambda x: (x.get("clip") or "", x.get("in") or 0)
    for kind in ("favorite", "reject"):
        items = [x for x in sel if x.get("kind") == kind]
        if not items:
            continue
        lines.append(f"**{t('material.md.' + kind)}**")
        lines.append("")
        for x in sorted(items, key=order):
            note = f" – {x['note']}" if x.get("note") else ""
            lines.append(f"- {x.get('clip')} ({x.get('file') or ''}), {when(x)}{note}")
        lines.append("")
    tags = sorted({x.get("tag") for x in sel if x.get("kind") == "tag" and x.get("tag")}, key=str.lower)
    if tags:
        lines += [f"**{t('material.md.tags')}**", ""]
        for tag in tags:
            items = sorted((x for x in sel if x.get("kind") == "tag" and x.get("tag") == tag), key=order)
            lines.append(f"- {tag}: " + "; ".join(
                f"{x.get('clip')} ({x.get('file') or ''}), {when(x)}" + (f" – {x['note']}" if x.get("note") else "")
                for x in items))
        lines.append("")
    wrong = load_hidden(paths)
    if wrong["all"] or wrong["clips"]:
        def name(key):
            label = t(f"tag.{key[0]}.{key[2:]}")
            return label if not label.startswith("tag.") else key[2:]
        parts = [f"{name(k)} ({t('material.md.everywhere')})" for k in wrong["all"]]
        parts += [f"{name(k)} ({cid})" for cid, keys in sorted(wrong["clips"].items()) for k in keys]
        lines += [f"**{t('material.md.wrong')}**", "", "; ".join(parts), ""]
    return lines


def _mmss(s) -> str:
    s = float(s or 0)
    return f"{int(s // 60)}:{s % 60:04.1f}"
