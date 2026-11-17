import json
import time
import tomllib

import pytest

from conftest import auth_header
from cutlery import cutter_project
from cutlery.analysis import AnalysisWorker
from cutlery.db import Database
from cutlery.projects import Projects
from test_sidecar import GOOD, variant


@pytest.fixture
def env(cfg):
    db = Database(cfg.database)
    projects = Projects(cfg, db)
    worker = AnalysisWorker(cfg, db, projects)
    yield projects, worker, db
    worker.close()


def make_uploaded_clip(projects, pid, name, head="a"):
    sc = variant(rel_path=f"Day/{name}", file=name, fingerprint={"size": GOOD["size"], "head_sha256": head * 64,
                                                               "tail_sha256": "b" * 64})
    status = projects.register(pid, sc)
    standin = projects.paths(pid).standin(f"Day/{name}")
    standin.parent.mkdir(parents=True, exist_ok=True)
    standin.write_bytes(b"x")
    projects.mark(pid, status["id"], "uploaded", None, 1)
    return status["id"]


def wait_for(predicate, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def lines(path):
    return path.read_text().splitlines() if path.exists() else []


def state(db, pid):
    return db.one("SELECT analysis_state, analysis_message FROM projects WHERE id=?", (pid,))


def test_cutter_toml_points_cutter_at_the_standins_and_sidecars(cfg, env):
    projects, _, _ = env
    p = projects.create("Reise “Ä”")
    paths = projects.paths(p["id"])
    conf = tomllib.loads(cutter_project.render(paths, p["name"]))
    assert conf["title"] == "Reise “Ä”"
    assert conf["media"]["standins"] == str(cfg.work_dir / "projects" / p["id"] / "media")
    assert conf["standins"]["sidecars"] == str(cfg.data_dir / "projects" / p["id"] / "sidecars")
    work = cfg.work_dir / "projects" / p["id"] / "analysis"
    assert conf["paths"]["clips"] == str(work / "clips.json")
    # everything cutter regenerates is in the work tree, nothing of it in the data tree
    assert all(str(v).startswith(str(cfg.work_dir)) for v in conf["paths"].values())


def test_analysis_runs_after_the_last_upload_and_only_once(cfg, env, monkeypatch, tmp_path):
    projects, worker, db = env
    runs = tmp_path / "runs.log"
    monkeypatch.setenv("FAKE_CUTTER_LOG", str(runs))
    pid = projects.create("T")["id"]
    for i in range(3):                                  # three uploads in quick succession
        make_uploaded_clip(projects, pid, f"c{i}.mp4", head="abc"[i])
        worker.request(pid)
    assert state(db, pid)["analysis_state"] in ("waiting", "running")
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "idle" and runs.exists())
    assert len(lines(runs)) == 1
    progress = cutter_project.progress(projects.paths(pid))
    assert (progress["clips"], progress["transcribed"], progress["analysed"]) == (3, 3, 3)
    assert not worker.pending(pid)
    assert (cfg.data_dir / "projects" / pid / "cutter.toml").is_file()


def test_a_request_during_a_run_causes_a_second_run_for_the_new_clip(cfg, env, monkeypatch, tmp_path):
    projects, worker, db = env
    runs = tmp_path / "runs.log"
    monkeypatch.setenv("FAKE_CUTTER_LOG", str(runs))
    monkeypatch.setenv("FAKE_CUTTER_SLEEP", "1")
    pid = projects.create("T")["id"]
    make_uploaded_clip(projects, pid, "a.mp4", head="a")
    worker.request(pid)
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "running")
    make_uploaded_clip(projects, pid, "b.mp4", head="b")
    worker.request(pid)
    assert wait_for(lambda: len(lines(runs)) == 2 and state(db, pid)["analysis_state"] == "idle", 20)
    assert cutter_project.progress(projects.paths(pid))["clips"] == 2
    # IDs stay the same across runs
    ids = [c["id"] for c in cutter_project.clips_json(projects.paths(pid))]
    assert sorted(ids) == ["C001", "C002"]


def test_failure_is_kept_with_the_reason_and_can_be_retried(cfg, env, monkeypatch):
    projects, worker, db = env
    pid = projects.create("T")["id"]
    make_uploaded_clip(projects, pid, "a.mp4")
    monkeypatch.setenv("FAKE_CUTTER_FAIL", "1")
    worker.request(pid)
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "failed")
    assert "simulated failure" in state(db, pid)["analysis_message"]
    assert "simulated failure" in (projects.paths(pid).work / "analysis.log").read_text()
    monkeypatch.delenv("FAKE_CUTTER_FAIL")
    worker.request(pid)
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "idle")
    assert cutter_project.progress(projects.paths(pid))["clips"] == 1


def test_missing_cutter_binary_is_a_clear_failure(cfg, env):
    projects, _, db = env
    worker = AnalysisWorker(cfg, db, projects, command=["/nonexistent/cutter", "analyze"], settle=0)
    pid = projects.create("T")["id"]
    make_uploaded_clip(projects, pid, "a.mp4")
    worker.request(pid)
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "failed")
    assert "cannot run /nonexistent/cutter" in state(db, pid)["analysis_message"]
    worker.close()


def test_cancel_stops_a_running_analysis_before_the_project_is_deleted(cfg, env, monkeypatch):
    projects, worker, db = env
    monkeypatch.setenv("FAKE_CUTTER_SLEEP", "30")
    pid = projects.create("T")["id"]
    make_uploaded_clip(projects, pid, "a.mp4")
    worker.request(pid)
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "running")
    t0 = time.time()
    worker.cancel(pid)
    assert time.time() - t0 < 10
    assert pid not in worker.jobs


def test_resume_after_a_restart_picks_up_unanalysed_clips(cfg, env, monkeypatch):
    projects, worker, db = env
    done = projects.create("Done")["id"]
    todo = projects.create("Todo")["id"]
    make_uploaded_clip(projects, done, "a.mp4")
    make_uploaded_clip(projects, todo, "a.mp4")
    worker.request(done)
    assert wait_for(lambda: state(db, done)["analysis_state"] == "idle" and not worker.pending(done))
    assert worker.pending(todo) is True
    worker2 = AnalysisWorker(cfg, db, projects, settle=0.1)
    assert worker2.resume() == 1
    assert wait_for(lambda: not worker2.pending(todo))
    worker2.close()


def test_a_run_interrupted_before_the_image_analysis_is_resumed(cfg, env):
    """An update restarts Cutlery while cutter is transcribing: every clip is in clips.json already, but the run
    is not done, so it continues after the restart."""
    projects, worker, db = env
    pid = projects.create("Half")["id"]
    make_uploaded_clip(projects, pid, "a.mp4")
    worker.request(pid)
    assert wait_for(lambda: state(db, pid)["analysis_state"] == "idle" and not worker.pending(pid))
    clip = cutter_project.clips_json(projects.paths(pid))[0]
    (cutter_project.analysis_dir(projects.paths(pid)) / "image" / f"{clip['id']}.json").unlink()
    assert worker.pending(pid) is True


def test_api_reports_the_progress(client, cfg, app):
    from test_app import TestProjects

    pid = TestProjects().create(client)
    r = client.get(f"/api/projects/{pid}/analysis").json()
    assert r["state"] == "idle" and r["clips"] == 0 and r["uploaded"] == 0
    assert client.get("/api/projects/nope/analysis").status_code == 404


def test_a_stopped_analysis_stays_stopped_after_a_restart(cfg, env):
    projects, worker, db = env
    pid = projects.create("Paused")["id"]
    make_uploaded_clip(projects, pid, "a.mp4")
    worker.stop(pid)
    worker2 = AnalysisWorker(cfg, db, projects, settle=0.1)
    assert worker2.resume() == 0 and state(db, pid)["analysis_state"] == "stopped"
    worker2.close()
