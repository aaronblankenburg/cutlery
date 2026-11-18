"""Export: a ZIP for the user's editing application whose clips point to the originals on the user's computer.

The browser has checked the originals against the manifest and sends the footage folder's path, the operating system
and, for originals that were renamed or moved since the upload, their new relative paths. `cutter export` writes the
package (timeline, subtitles, music, title pictures, maps); Cutlery adds the import instructions and the music licences
in the UI language and streams it as a ZIP. Nothing is kept: the package is built at download time.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
import zipfile

from . import cutter_project, i18n
from .storage import ProjectPaths

log = logging.getLogger("cutlery.export")

FORMATS = {"premiere": {"ready": True}, "fcp": {"ready": True}, "resolve": {"ready": True}, "filmcraft": {"ready": True}}
OSES = ("windows", "macos", "linux")


class ExportError(Exception):
    """The export could not be made; the text is for the user (English, logged) and `key` names a UI message."""

    def __init__(self, text, key="export.error.failed"):
        super().__init__(text)
        self.key = key


def safe_name(text: str) -> str:
    import re
    return re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_") or "film"


def check_request(form: dict, cuts: list[str]) -> dict:
    fmt = form.get("format", "premiere")
    if fmt not in FORMATS or not FORMATS[fmt]["ready"]:
        raise ExportError(f"format {fmt} is not available", "export.error.format")
    if form.get("os") not in OSES:
        raise ExportError("operating system missing", "export.error.os")
    root = (form.get("root") or "").strip()
    if not root:
        raise ExportError("footage path missing", "export.error.root")
    cut = form.get("cut") or (cuts[-1] if cuts else "")
    if cut not in cuts:
        raise ExportError(f"cut {cut!r} not found", "export.error.cut")
    try:
        relink = json.loads(form.get("relink") or "{}")
    except ValueError:
        raise ExportError("relink is not JSON")
    if not isinstance(relink, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in relink.items()):
        raise ExportError("relink must map paths to paths")
    return {"format": fmt, "os": form["os"], "root": root, "cut": cut, "relink": relink}


def build(cfg, paths: ProjectPaths, title: str, req: dict, lang: str) -> pathlib.Path:
    """Run cutter export and pack the result. Returns the path of a ZIP in the work tree (the caller deletes it)."""
    cutter_project.write(paths, title)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="export-", dir=paths.work / "tmp"))
    try:
        pkg = tmp / "package"
        relink = tmp / "relink.json"
        relink.write_text(json.dumps(req["relink"], ensure_ascii=False), encoding="utf-8")
        cmd = [cfg.cutter_bin, "export", req["cut"], "--to", req["format"], "--target-os", req["os"],
               "--target-root", req["root"], "--out", str(pkg), "--relink", str(relink), "--json"]
        env = {**os.environ, "CUTTER_PROJECT": str(paths.data), "TMPDIR": str(tmp)}
        r = subprocess.run(cmd, cwd=paths.data, env=env, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            log.error("export of %s failed: %s", paths.id, (r.stderr or r.stdout)[-2000:])
            raise ExportError(_last_line(r.stderr) or "cutter export failed")
        report = json.loads(r.stdout or "{}")
        t = lambda key, **v: i18n.translate(lang, key, **v)
        readme = pkg / t(f"export.readme.{req['format']}.file")
        readme.write_text(readme_text(t, req, report, title), encoding="utf-8")
        if report.get("music"):
            lic = pkg / "music" / t("export.licenses.file")
            if lic.name != "LICENSES.txt":
                (pkg / "music" / "LICENSES.txt").unlink(missing_ok=True)
            lic.write_text(licenses_text(t, report["music"]), encoding="utf-8")
        target = tmp.parent / f"{tmp.name}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(pkg.rglob("*")):
                # hidden files (FilmCraft's .<project>.fcproj.lock) are not part of the package
                if f.is_file() and not any(x.startswith(".") for x in f.relative_to(pkg).parts):
                    # audio and pictures are compressed already
                    kind = zipfile.ZIP_STORED if f.suffix.lower() in (".png", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".jpg") else zipfile.ZIP_DEFLATED
                    z.write(f, f.relative_to(pkg).as_posix(), compress_type=kind)
        return target
    except subprocess.TimeoutExpired:
        raise ExportError("cutter export took too long")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _last_line(text: str) -> str:
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    return lines[-1][-300:] if lines else ""


def readme_text(t, req, report, title):
    sep = "\\" if req["os"] == "windows" else "/"
    folder = req["root"].rstrip("\\/") + sep + "_cutlery"
    lines = [t(f"export.readme.{req['format']}.title", title=title, cut=req["cut"]), "",
             t("export.readme.where", folder=folder), "",
             t(f"export.readme.{req['format']}.steps", timeline=report.get("timeline", ""),
               srt=report.get("timeline", "").rsplit(".", 1)[0] + ".srt"), "",
             t(f"export.readme.{req['format']}.not_included"), ""]
    if report.get("notes"):
        lines += [t("export.readme.notes"), *[f"- {n}" for n in report["notes"]], ""]
    lines.append(t("export.readme.footer"))
    return "\n".join(lines) + "\n"


def licenses_text(t, music):
    lines = [t("export.licenses.title"), ""]
    for m in music:
        lines.append(f'{m.get("cue") or ""}  {m.get("title") or ""} – {m.get("artist") or ""}')
        lines.append("    " + t("export.licenses.license", license=m.get("license") or "?"))
        if m.get("source"):
            lines.append("    " + t("export.licenses.source", source=m["source"]))
        if m.get("file"):
            lines.append("    " + t("export.licenses.file_line", file=pathlib.PurePosixPath(m["file"]).name))
        lines.append("")
    return "\n".join(lines) + "\n"
