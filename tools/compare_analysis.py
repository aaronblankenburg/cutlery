#!/usr/bin/env python3
"""Compare cutter's image analysis of the original clips with that of their 360p stand-ins.

Used to decide whether cutter's thresholds (quality.py, image_analysis.py) behave differently on stand-ins
(docs/standin-analysis.md). Needs two analysis folders that cutter wrote for the SAME clips:

    compare_analysis.py ORIGINAL_ANALYSIS STANDIN_ANALYSIS [--json]

where each folder holds clips.json and image/<id>.json. Clips are matched by the original's file name, which must be unique in the set (a stand-in analysis
states it too, see standin.py).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys


def load(folder: pathlib.Path):
    clips = json.loads((folder / "clips.json").read_text())
    out = {}
    for c in clips:
        f = folder / "image" / f"{c['id']}.json"
        if f.is_file():
            out[c["file"]] = (c, json.loads(f.read_text()))
    return out


def flag_seconds(res, kind):
    """Seconds (integers) covered by flaws that name `kind`."""
    secs = set()
    for fl in res.get("flaws", []):
        if kind in fl.get("why", []):
            secs.update(range(int(fl["start"]), int(fl["end"]) + 1))
    return secs


def usable_seconds(res):
    secs = set()
    for u in res.get("usable", []):
        secs.update(range(int(u["start"]), int(u["end"]) + 1))
    return secs


def move_at(res):
    out = {}
    for seg in res.get("camera", []):
        for s in range(int(seg["start"]), max(int(seg["start"]) + 1, int(seg["end"]))):
            out[s] = seg["move"]
    return out


def agreement(a: set, b: set, universe: int):
    """Share of the clip's seconds on which both say the same thing."""
    if universe <= 0:
        return None
    return 1 - len(a ^ b) / universe


def compare(orig, stand):
    rows = []
    for rel, (oc, o) in sorted(orig.items()):
        hit = stand.get(rel)
        if not hit:
            continue
        sc, s = hit
        dur = int(o["duration"]) + 1
        om, sm = move_at(o), move_at(s)
        common = set(om) & set(sm)
        row = {
            "clip": rel, "duration": round(o["duration"], 1),
            "original_px": f"{oc.get('width')}x{oc.get('height')}",
            "sharpness_original": o["summary"].get("sharpness_mean"), "sharpness_standin": s["summary"].get("sharpness_mean"),
            "usable_original": o["summary"].get("usable_s"), "usable_standin": s["summary"].get("usable_s"),
            "usable_agreement": agreement(usable_seconds(o), usable_seconds(s), dur),
            "soft_agreement": agreement(flag_seconds(o, "soft"), flag_seconds(s, "soft"), dur),
            "blurred_agreement": agreement(flag_seconds(o, "blurred"), flag_seconds(s, "blurred"), dur),
            "soft_s_original": len(flag_seconds(o, "soft")), "soft_s_standin": len(flag_seconds(s, "soft")),
            "move_agreement": (sum(1 for t in common if om[t] == sm[t]) / len(common)) if common else None,
            "scenes_original": o["summary"].get("scenes"), "scenes_standin": s["summary"].get("scenes"),
            "faces_original": o["summary"].get("faces_max"), "faces_standin": s["summary"].get("faces_max"),
            "people_original": o["summary"].get("people_max"), "people_standin": s["summary"].get("people_max"),
            "exposure_original": o["summary"].get("exposure_mean"), "exposure_standin": s["summary"].get("exposure_mean"),
            "shake_original": o["summary"].get("shake_mean"), "shake_standin": s["summary"].get("shake_mean"),
            "low_detail_original": bool(o["summary"].get("low_detail")), "low_detail_standin": bool(s["summary"].get("low_detail")),
        }
        rows.append(row)
    return rows


def mean(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.mean(v), 3) if v else None


def summary(rows):
    ratios = [r["sharpness_standin"] / r["sharpness_original"] for r in rows
              if r["sharpness_original"] and r["sharpness_standin"] is not None]
    return {
        "clips": len(rows),
        "sharpness_ratio_standin_to_original": round(statistics.median(ratios), 3) if ratios else None,
        "usable_agreement": mean(rows, "usable_agreement"), "soft_agreement": mean(rows, "soft_agreement"),
        "blurred_agreement": mean(rows, "blurred_agreement"), "move_agreement": mean(rows, "move_agreement"),
        "scenes_equal": sum(1 for r in rows if r["scenes_original"] == r["scenes_standin"]),
        "faces_max_original": sum(r["faces_original"] or 0 for r in rows), "faces_max_standin": sum(r["faces_standin"] or 0 for r in rows),
        "people_max_original": sum(r["people_original"] or 0 for r in rows), "people_max_standin": sum(r["people_standin"] or 0 for r in rows),
        "soft_s_original": sum(r["soft_s_original"] for r in rows), "soft_s_standin": sum(r["soft_s_standin"] for r in rows),
        "usable_s_original": round(sum(r["usable_original"] or 0 for r in rows), 1),
        "usable_s_standin": round(sum(r["usable_standin"] or 0 for r in rows), 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("original", type=pathlib.Path)
    ap.add_argument("standin", type=pathlib.Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = compare(load(a.original), load(a.standin))
    if not rows:
        sys.exit("No clip is in both analyses (matched by file name).")
    out = {"summary": summary(rows), "clips": rows}
    if a.json:
        print(json.dumps(out, indent=1))
        return
    for k, v in out["summary"].items():
        print(f"{k:42} {v}")
    print()
    for r in rows:
        print(f"{r['clip'][:44]:44} {r['original_px']:>10}  sharp {r['sharpness_original']}→{r['sharpness_standin']}"
              f"  usable {r['usable_original']}→{r['usable_standin']} (agree {r['usable_agreement'] and round(r['usable_agreement'], 2)})"
              f"  soft {r['soft_s_original']}→{r['soft_s_standin']}  moves {r['move_agreement'] and round(r['move_agreement'], 2)}"
              f"  scenes {r['scenes_original']}→{r['scenes_standin']}  faces {r['faces_original']}→{r['faces_standin']}")


if __name__ == "__main__":
    main()
