"""The project pages after docs/design (status, setup, review, analysis) and their small APIs."""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import tempfile

import anyio
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import briefing, cutter_project, eta, files, i18n, overview
from .jobs import JobError
from .projects import NotFound

PICTURE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.(jpg|png)$")
ANALYSIS_KINDS = {"sheets": ".jpg", "strips": ".jpg", "overview": ".jpg", "faces": ".jpg"}


class Context:
    """What the routes need from the app (set up in app.create_app)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def routes(c: Context):
    projects, jobs, reviews, worker, cfg = c.projects, c.jobs, c.reviews, c.worker, c.cfg

    def t_for(request):
        lang = c.lang_of(request)
        return lambda key, **v: i18n.translate(lang, key, **v)

    async def state_of(p):
        return await anyio.to_thread.run_sync(overview.state, projects, worker, jobs, reviews, p)

    # ------------------------------------------------------------ pages

    async def status_page(request):
        p = c.project_or_404(request.path_params["pid"])
        st = await state_of(p)
        paths = projects.paths(p["id"])
        facts = await anyio.to_thread.run_sync(overview.analysis_facts, projects, p["id"])
        people = await anyio.to_thread.run_sync(overview.people, paths)
        remaining = await anyio.to_thread.run_sync(eta.remaining, p, paths, c.cfg.work_dir)
        return c.page(request, "status.html", project=p, st=st, facts=facts, people=people, remaining=remaining)

    async def setup_page(request):
        p = c.project_or_404(request.path_params["pid"])
        st = await state_of(p)
        return c.page(request, "setup.html", project=p, st=st)

    async def footage_redirect(request):
        return RedirectResponse(f"/projects/{request.path_params['pid']}/setup", status_code=301)

    async def review_page(request):
        p = c.project_or_404(request.path_params["pid"])
        st = await state_of(p)
        return c.page(request, "review.html", project=p, st=st)

    async def analysis_page(request):
        p = c.project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        clips = await anyio.to_thread.run_sync(cutter_project.clips_json, paths)
        a = cutter_project.analysis_dir(paths)
        # cutter's clips by Cutlery's: same rel_path. A clip deleted since the last analysis run is not shown.
        ours = {x["rel_path"]: x["id"] for x in await anyio.to_thread.run_sync(projects.clips, p["id"])}
        rows = []
        for cl in clips:
            if cl.get("rel_path") not in ours:
                continue
            # words from the transcript itself: cutter writes them into clips.json only at the end of a run
            transcript, words, transcribed = "", 0, False
            try:
                tr = json.loads((a / "transcripts" / f"{cl['id']}.json").read_text(encoding="utf-8"))
                segs = [s for s in tr.get("segments", []) if not s.get("suspect")]
                transcript = " ".join(s.get("text", "").strip() for s in segs)
                words = sum(len(s.get("words") or []) or len(s.get("text", "").split()) for s in segs)
                transcribed = True
            except (OSError, ValueError):
                pass
            rows.append({"id": cl["id"], "clip_id": ours[cl["rel_path"]], "file": cl.get("file"), "rel_path": cl.get("rel_path"), "duration": cl.get("duration"),
                         "created": cl.get("created"), "sheet": (a / "sheets" / f"{cl['id']}.jpg").is_file(),
                         "transcript": transcript[:3000], "words": words, "device": cl.get("device"),
                         "transcribed": transcribed or not cl.get("has_audio", True)})
        quality = ""
        try:
            quality = (a / "quality.txt").read_text(encoding="utf-8")
        except OSError:
            pass
        overview_pages = sorted(f.name for f in (a / "overview").glob("*.jpg")) if (a / "overview").is_dir() else []
        return c.page(request, "analysis.html", project=p, clips=rows, quality=quality, overview_pages=overview_pages)

    async def analysis_file(request):
        p = c.project_or_404(request.path_params["pid"])
        kind, name = request.path_params["kind"], request.path_params["name"]
        if kind not in ANALYSIS_KINDS or not PICTURE.match(name):
            raise HTTPException(404)
        a = cutter_project.analysis_dir(projects.paths(p["id"]))
        f = (a / "image" / "faces" / name) if kind == "faces" else (a / kind / name)
        if not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"cache-control": "private, max-age=300"})

    async def analysis_clip(request):
        """A clip's 360p copy, for the player on the analysis page (range requests: FileResponse)."""
        p = c.project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        clips = await anyio.to_thread.run_sync(cutter_project.clips_json, paths)
        cl = next((x for x in clips if x.get("id") == request.path_params["cid"]), None)
        if not cl or not cl.get("rel_path"):
            raise HTTPException(404)
        f = paths.standin(cl["rel_path"]).resolve()
        if not f.is_relative_to(paths.media.resolve()) or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, media_type="video/mp4", headers={"cache-control": "private, max-age=300"})

    async def thumb(request):
        """A still of the project for the project list: the n-th clip's contact sheet."""
        p = c.project_or_404(request.path_params["pid"])
        n = int(request.path_params["n"])
        a = cutter_project.analysis_dir(projects.paths(p["id"]))
        sheets = sorted((a / "sheets").glob("*.jpg")) if (a / "sheets").is_dir() else []
        if not sheets:
            raise HTTPException(404)
        f = sheets[min(len(sheets) - 1, n * max(1, len(sheets) // 3))]
        return FileResponse(f, headers={"cache-control": "private, max-age=600"})

    # ------------------------------------------------------------ project state

    async def api_state(request):
        p = c.project_or_404(request.path_params["pid"])
        st = await state_of(p)
        st.pop("briefing", None)
        return JSONResponse(st)

    # ------------------------------------------------------------ files

    async def api_files(request):
        p = c.project_or_404(request.path_params["pid"])
        return JSONResponse(await anyio.to_thread.run_sync(files.listing, projects.paths(p["id"])))

    async def api_file(request):
        p = c.project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        kind, raw = request.path_params["kind"], request.path_params["name"]
        try:
            if request.method == "DELETE":
                await anyio.to_thread.run_sync(files.remove, paths, kind, raw)
            else:
                size = int(request.headers.get("content-length") or 0)
                name = files.check(kind, raw, size)
                target = files.folder(paths, kind)
                target.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=target, prefix=".upload-")
                written = 0
                try:
                    with os.fdopen(fd, "wb") as out:
                        async for chunk in request.stream():
                            written += len(chunk)
                            if written > files.KINDS[kind]["max"]:
                                raise files.FileError("file too large")
                            await anyio.to_thread.run_sync(out.write, chunk)
                    os.replace(tmp, target / name)
                except BaseException:
                    pathlib.Path(tmp).unlink(missing_ok=True)
                    raise
        except files.FileError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        await anyio.to_thread.run_sync(briefing.refresh, paths, t_for(request), p["name"])
        return JSONResponse(await anyio.to_thread.run_sync(files.listing, paths))

    async def api_license(request):
        p = c.project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        data = await c.read_json(request)
        try:
            await anyio.to_thread.run_sync(files.set_license, paths, request.path_params["name"], str(data.get("license")))
        except files.FileError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        await anyio.to_thread.run_sync(briefing.refresh, paths, t_for(request), p["name"])
        return JSONResponse(await anyio.to_thread.run_sync(files.listing, paths))

    # ------------------------------------------------------------ people

    async def api_people(request):
        p = c.project_or_404(request.path_params["pid"])
        return JSONResponse(await anyio.to_thread.run_sync(overview.people, projects.paths(p["id"])))

    async def api_person(request):
        p = c.project_or_404(request.path_params["pid"])
        pid_, person = p["id"], request.path_params["person"]
        if not re.fullmatch(r"P\d{1,4}", person):
            raise HTTPException(404)
        name = " ".join(str((await c.read_json(request)).get("name") or "").split())[:60]
        paths = projects.paths(pid_)
        env = {**os.environ, "CUTTER_PROJECT": str(paths.data)}

        def rename():
            cutter_project.write(paths, p["name"])
            return subprocess.run([cfg.cutter_bin, "people", "name", person, name, "--json"], cwd=paths.data, env=env,
                                  capture_output=True, text=True, timeout=60)

        r = await anyio.to_thread.run_sync(rename)
        if r.returncode != 0:
            return JSONResponse({"error": (r.stderr or r.stdout).strip()[-300:]}, status_code=422)
        return JSONResponse(await anyio.to_thread.run_sync(overview.people, paths))

    # ------------------------------------------------------------ review

    async def review_api(request):
        p = c.project_or_404(request.path_params["pid"])
        path = "/api/" + request.path_params["path"]
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 65536:
                raise HTTPException(413, "request too large")
        user = (request.scope.get("state") or {}).get("user") or "cutlery"
        status, ctype, data = await anyio.to_thread.run_sync(
            reviews.api, p["id"], request.method, path, request.url.query, body, user)
        return Response(data, status_code=status, media_type=ctype, headers={"cache-control": "no-store"})

    async def review_media(request):
        p = c.project_or_404(request.path_params["pid"])
        f = await anyio.to_thread.run_sync(reviews.media, p["id"], request.path_params["cut"], request.path_params["name"])
        if f is None:
            raise HTTPException(404)
        return FileResponse(f, headers={"cache-control": "private, max-age=60"})

    async def review_done(request):
        """"Review done → next cut": lock the cut's comments and queue the next cut."""
        p = c.project_or_404(request.path_params["pid"])
        cut = str((await c.read_json(request)).get("cut") or "")
        cuts = cutter_project.cuts(projects.paths(p["id"]))
        if not cuts or cut != cuts[-1]:
            return JSONResponse({"error": "only the newest cut can be finished", "message":
                                 i18n.translate(c.lang_of(request), "review.error.not_newest")}, status_code=409)
        await anyio.to_thread.run_sync(reviews.lock_cut, p["id"], cut)
        try:
            j = await anyio.to_thread.run_sync(jobs.create, p["id"], "next")
        except JobError as e:
            await anyio.to_thread.run_sync(reviews.unlock_cut, p["id"], cut)
            return JSONResponse({"error": str(e), "message": i18n.translate(c.lang_of(request), e.key)}, status_code=409)
        return JSONResponse({"job": j["id"], "cut": j["cut"]}, status_code=201)

    return [
        Route("/projects/{pid}", status_page),
        Route("/projects/{pid}/setup", setup_page),
        Route("/projects/{pid}/footage", footage_redirect),
        Route("/projects/{pid}/review", review_page),
        Route("/projects/{pid}/analysis", analysis_page),
        Route("/projects/{pid}/analysis/clip/{cid}.mp4", analysis_clip),
        Route("/projects/{pid}/analysis/{kind}/{name}", analysis_file),
        Route("/projects/{pid}/thumb/{n:int}", thumb),
        Route("/projects/{pid}/review/api/{path:path}", review_api, methods=["GET", "POST", "PATCH", "DELETE"]),
        Route("/projects/{pid}/review/media/{cut}/{name}", review_media),
        Route("/api/projects/{pid}/state", api_state),
        Route("/api/projects/{pid}/files", api_files),
        Route("/api/projects/{pid}/files/{kind}/{name}", api_file, methods=["PUT", "DELETE"]),
        Route("/api/projects/{pid}/music/{name}/license", api_license, methods=["PUT"]),
        Route("/api/projects/{pid}/people", api_people),
        Route("/api/projects/{pid}/people/{person}", api_person, methods=["PUT"]),
        Route("/api/projects/{pid}/review/done", review_done, methods=["POST"]),
    ]
