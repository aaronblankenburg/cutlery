"""Storage layout and the start-up check of the three storage trees.

    DATA_DIR/                     keep: back this up
      cutlery.sqlite, htpasswd
      projects/<id>/              cutter project folder (cutter.toml, briefing, cuts, comments …)
        sidecars/<rel_path>.json  original metadata per clip (written by the browser)
        sidecars/<rel_path>.gpmd  raw GoPro telemetry track, .SRT DJI flight data
        manifest.json             all clips with their fingerprints (used to match originals at export)
    WORK_DIR/                     regenerable
      projects/<id>/media/<rel_path>.mp4   360p stand-ins
      projects/<id>/…             analysis, renders, exports, temporary files of jobs
      uploads/                    unfinished uploads
    MODELS_DIR/                   downloaded models, shared by all projects
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import shutil
import tempfile

from .config import Config, ConfigError

TREES = (("CUTLERY_DATA_DIR", "data_dir"), ("CUTLERY_WORK_DIR", "work_dir"), ("CUTLERY_MODELS_DIR", "models_dir"))


def check_trees(cfg: Config) -> None:
    """Every storage tree must exist and be writable by this process; never create them silently."""
    problems = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for var, attr in TREES:
        path = getattr(cfg, attr)
        if not path.is_dir():
            problems.append(f"{var}: {path} does not exist. Create it on the host (as the user PUID in .env) "
                            f"and set the path in .env.")
            continue
        try:
            with tempfile.NamedTemporaryFile(dir=path, prefix=".cutlery-write-test-"):
                pass
        except OSError:
            owner = path.stat().st_uid
            if owner == uid:
                problems.append(f"{var}: {path} belongs to user {uid} but is not writable. "
                                f"Fix it with: chmod u+rwx <host path> (and check that the disk is not mounted read-only).")
            else:
                problems.append(f"{var}: {path} is not writable by user {uid} (it belongs to user {owner}). "
                                f"Fix it with: chown -R {uid} <host path>, or set PUID in .env to the owner.")
    if problems:
        raise ConfigError("Storage is not ready:\n  " + "\n  ".join(problems))


@dataclasses.dataclass(frozen=True)
class DiskSpace:
    name: str
    path: str
    free: int
    total: int
    low: bool


def disk_space(cfg: Config) -> list[DiskSpace]:
    out = []
    for name, attr in (("data", "data_dir"), ("work", "work_dir"), ("models", "models_dir")):
        path = getattr(cfg, attr)
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        out.append(DiskSpace(name, str(path), usage.free, usage.total, usage.free < cfg.disk_warn_gb * 1e9))
    return out


class ProjectPaths:
    """Where one project's files live in the two trees."""

    def __init__(self, cfg: Config, project_id: str):
        if not project_id.isalnum():
            raise ValueError("invalid project id")
        self.id = project_id
        self.data = cfg.data_dir / "projects" / project_id
        self.work = cfg.work_dir / "projects" / project_id

    @property
    def sidecars(self) -> pathlib.Path:
        return self.data / "sidecars"

    @property
    def media(self) -> pathlib.Path:
        return self.work / "media"

    @property
    def manifest(self) -> pathlib.Path:
        return self.data / "manifest.json"

    def create(self) -> None:
        for d in (self.data, self.sidecars, self.work, self.media, self.work / "tmp"):
            d.mkdir(parents=True, exist_ok=True)

    def remove(self) -> None:
        shutil.rmtree(self.data, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    def standin(self, rel_path: str) -> pathlib.Path:
        return self.media / (rel_path + ".mp4")

    def sidecar(self, rel_path: str, suffix: str = ".json") -> pathlib.Path:
        return self.sidecars / (rel_path + suffix)


def write_atomic(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
