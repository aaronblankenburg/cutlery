#!/usr/bin/env python3
"""A stand-in for `cutter analyze` in the unit tests: reads cutter.toml, writes clips.json and one transcript and
image analysis per clip from the sidecars, the way cutter would (IDs C001… by order of first sight, kept by
fingerprint). Controlled by the environment:

    FAKE_CUTTER_FAIL=1     print a message and exit with code 3
    FAKE_CUTTER_SLEEP=2    seconds to take
    FAKE_CUTTER_LOG=file   append one line per call (to count runs)
"""
import json
import os
import pathlib
import sys
import time
import tomllib

if sys.argv[1:2] == ["export"]:
    # cutter export <cut> --to F --target-os OS --target-root ROOT --out DIR --relink FILE --json
    args = dict(zip(sys.argv[3::2], sys.argv[4::2]))
    if os.environ.get("FAKE_CUTTER_FAIL"):
        print("Error: simulated export failure", file=sys.stderr)
        sys.exit(1)
    out = pathlib.Path(args["--out"])
    (out / "music").mkdir(parents=True, exist_ok=True)
    relink = json.loads(pathlib.Path(args["--relink"]).read_text())
    suffix = {"premiere": ".xml", "fcp": ".fcpxml", "resolve": ".fcpxml", "filmcraft": ".fcproj"}[args["--to"]]
    timeline = f"Film_{sys.argv[2]}{suffix}"
    (out / timeline).write_text(json.dumps({"os": args["--target-os"], "root": args["--target-root"],
                                            "relink": relink, "to": args["--to"]}))
    (out / "music" / "LICENSES.txt").write_text("english licences")
    (out / "music" / "song.mp3").write_bytes(b"ID3")
    print(json.dumps({"timeline": timeline, "notes": [], "music": [
        {"cue": "M1", "title": "Song", "artist": "Band", "license": "CC BY 4.0", "source": "https://example.org/song",
         "file": "music/song.mp3"}]}))
    sys.exit(0)
if sys.argv[1:2] == ["people"]:
    # options first, then "--" and the arguments (as Cutlery calls it)
    sys.argv = sys.argv[:3] + [a for a in sys.argv[3:] if a not in ("--json", "--")]
if sys.argv[1:3] == ["people", "name"]:
    # cutter people name P1 "Anna": rename a person in people.json
    project = pathlib.Path(os.environ["CUTTER_PROJECT"])
    conf = tomllib.loads((project / "cutter.toml").read_text(encoding="utf-8"))
    f = pathlib.Path(conf["paths"]["clips"]).parent / "people.json"
    data = json.loads(f.read_text())
    hit = next((p for p in data["people"] if p["id"] == sys.argv[3]), None)
    if hit is None:
        print(f"Error: {sys.argv[3]} is not in people.json", file=sys.stderr)
        sys.exit(3)
    hit["name"] = sys.argv[4]
    f.write_text(json.dumps(data))
    print(json.dumps({"id": hit["id"], "name": hit["name"]}))
    sys.exit(0)
if sys.argv[1:3] == ["people", "merge"]:
    # cutter people merge P1 P2 …: the first ID stays and takes the faces and clips of the others
    project = pathlib.Path(os.environ["CUTTER_PROJECT"])
    conf = tomllib.loads((project / "cutter.toml").read_text(encoding="utf-8"))
    f = pathlib.Path(conf["paths"]["clips"]).parent / "people.json"
    data = json.loads(f.read_text())
    ids = [a for a in sys.argv[3:] if not a.startswith("--")]
    found = [next((p for p in data["people"] if p["id"] == i), None) for i in ids]
    if None in found:
        print("Error: no such person", file=sys.stderr)
        sys.exit(3)
    first = found[0]
    for p in found[1:]:
        first["name"] = first["name"] or p["name"]
        first["seconds"] = first.get("seconds", 0) + p.get("seconds", 0)
        first["clips"] = {**p.get("clips", {}), **first.get("clips", {})}
        first["pictures"] = first.get("pictures", []) + p.get("pictures", [])
        data["people"].remove(p)
    f.write_text(json.dumps(data))
    print(json.dumps({"id": first["id"], "name": first["name"]}))
    sys.exit(0)
if sys.argv[1:2] != ["analyze"]:
    sys.exit("fake cutter: only analyze, export and people name/merge")
if os.environ.get("FAKE_CUTTER_LOG"):
    with open(os.environ["FAKE_CUTTER_LOG"], "a") as f:
        f.write(f"{os.getcwd()} {os.environ.get('CUTTER_PROJECT')}\n")
time.sleep(float(os.environ.get("FAKE_CUTTER_SLEEP", "0")))
if os.environ.get("FAKE_CUTTER_FAIL"):
    print("! cannot analyse: simulated failure", file=sys.stderr)
    sys.exit(3)

project = pathlib.Path(os.environ["CUTTER_PROJECT"])
conf = tomllib.loads((project / "cutter.toml").read_text(encoding="utf-8"))
sidecars, media = pathlib.Path(conf["standins"]["sidecars"]), pathlib.Path(conf["media"]["standins"])
clips_path = pathlib.Path(conf["paths"]["clips"])
out = clips_path.parent
known = {}
if clips_path.exists():
    for c in json.loads(clips_path.read_text()):
        fp = c["original"]["fingerprint"]
        known[(fp["size"], fp["head_sha256"], fp["tail_sha256"])] = c["id"]
clips = []
for sc_file in sorted(sidecars.rglob("*.json")):
    sc = json.loads(sc_file.read_text())
    stand_in = media / (sc["rel_path"] + ".mp4")
    if not stand_in.is_file():
        continue
    fp = sc["fingerprint"]
    key = (fp["size"], fp["head_sha256"], fp["tail_sha256"])
    if key not in known:
        known[key] = f"C{len(known) + 1:03d}"
    clips.append({"id": known[key], "file": sc["file"], "rel_path": sc["rel_path"], "path": str(stand_in),
                  "duration": sc["duration"], "original": {"fingerprint": fp}})
(out / "transcripts").mkdir(parents=True, exist_ok=True)
(out / "image").mkdir(parents=True, exist_ok=True)
for c in clips:
    (out / "transcripts" / f"{c['id']}.json").write_text("{}")
    (out / "image" / f"{c['id']}.json").write_text("{}")
clips_path.write_text(json.dumps(clips))
print(json.dumps({"clips": len(clips)}))
