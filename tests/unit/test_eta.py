"""The remaining analysis time, from the speed each stage shows on this machine."""
import json
import os
import types

from cutlery import eta


def setup(tmp_path, n=10, dur=60.0):
    paths = types.SimpleNamespace(work=tmp_path / "work")
    a = paths.work / "analysis"
    for d in ("sheets", "transcripts", "image"):
        (a / d).mkdir(parents=True)
    clips = [{"id": f"C{i:03d}", "duration": dur, "has_audio": i != 9} for i in range(n)]
    (a / "clips.json").write_text(json.dumps(clips))
    return paths, a


def touch(f, t):
    f.write_text("x")
    os.utime(f, (t, t))


def test_running_stage_measures_its_own_speed_and_stored_rates_cover_the_next(tmp_path):
    paths, a = setup(tmp_path)
    rates = tmp_path / "rates.json"
    t0 = 1_000_000.0
    for i in range(10):                              # sheets: all done in this run
        touch(a / "sheets" / f"C{i:03d}.jpg", t0 + i)
    for i in range(4):                               # transcripts: 4 done, 30 s each per 60 s clip
        touch(a / "transcripts" / f"C{i:03d}.json", t0 + 100 + 30 * i)
    r = eta.estimate(paths, t0 - 1, rates)
    assert r["stages"]["transcripts"]["cost"] == 0.5
    assert r["seconds"] is None                      # the image stage's speed is not known yet
    rates.write_text(json.dumps({"image": 0.25, "transcripts": 9}))
    r = eta.estimate(paths, t0 - 1, rates)
    # transcripts: 5 clips with sound left (C009 has none) x 60 s x 0.5; image: 10 x 60 x 0.25
    assert r["seconds"] == 5 * 60 * 0.5 + 10 * 60 * 0.25
    assert json.loads(rates.read_text())["transcripts"] == 0.5   # learnt, stored for next time


def test_earlier_runs_count_without_the_pauses_between_them(tmp_path):
    paths, a = setup(tmp_path, n=6)
    t0 = 1_000_000.0
    for i, t in enumerate((0, 60, 120, 5000, 5060, 5120)):    # two runs, 60 s per clip, a long pause between
        touch(a / "image" / f"C{i:03d}.json", t0 + t)
    r = eta.estimate(paths, t0 + 10_000, tmp_path / "rates.json")
    assert r["stages"]["image"]["cost"] == 1.0


def test_not_running_means_no_estimate(tmp_path):
    assert eta.remaining({"analysis_state": "idle", "analysis_updated": None}, None, tmp_path) is None
