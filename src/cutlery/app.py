"""The web application: pages, JSON API, uploads. Everything behind Basic Auth (see auth.py)."""
from __future__ import annotations

import contextlib
import re
import importlib.resources
import json
import logging
import urllib.parse

import anyio
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from markupsafe import Markup, escape
from starlette.templating import Jinja2Templates

from . import __version__, i18n
from . import config as config_mod
from . import cutter_project
from . import eta
from . import export as export_mod
from . import briefing as briefing_mod
from .analysis import AnalysisWorker
from .executor import LocalExecutor, RunnerClient, RunnerError
from .jobs import JobError, Jobs
from .notify import Notifier
from .telegram import Telegram, TelegramError
from . import egress, memory, overview, pages
from .review import Reviews
from .auth import BasicAuthMiddleware
from .share import Shares
from .db import Database
from .projects import NotFound, Projects
from .sidecar import SidecarError
from .storage import check_trees, disk_space
from .tus import TusApp

log = logging.getLogger("cutlery")
PKG = importlib.resources.files("cutlery")
MAX_JSON = 512 * 1024

# 'wasm-unsafe-eval' and blob: workers: the WebAssembly AAC encoder for browsers without a native one (vendor/VENDOR.md)
CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
       "worker-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class SecurityHeaders:
    """Strict headers on every response, and same-origin checks for every request that changes something
    (the browser sends Basic Auth to any site's forms, so cross-site requests must be refused)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] not in SAFE_METHODS and not _same_origin(scope):
            resp = Response("Cross-site request refused.\n", status_code=403, media_type="text/plain")
            return await resp(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                for k, v in ((b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
                             (b"referrer-policy", b"same-origin"), (b"x-frame-options", b"DENY"),
                             (b"cross-origin-opener-policy", b"same-origin"),
                             (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")):
                    if k not in names:
                        headers.append((k, v))
                message["headers"] = headers
            await send(message)

        return await self.app(scope, receive, send_with_headers)


def _same_origin(scope) -> bool:
    headers = {k.lower(): v for k, v in scope.get("headers") or []}
    site = headers.get(b"sec-fetch-site")
    if site is not None:
        return site in (b"same-origin", b"none")
    origin = headers.get(b"origin")
    if origin is None:
        return True          # not a browser (curl, tests); Basic Auth still applies
    host = headers.get(b"x-forwarded-host") or headers.get(b"host") or b""
    return urllib.parse.urlsplit(origin.decode("latin-1")).netloc.encode("latin-1") == host.split(b",")[0].strip()


def create_app(cfg: config_mod.Config | None = None, *, check_storage: bool = True,
               health_token: str | None = None) -> Starlette:
    cfg = cfg or config_mod.load()
    if check_storage:
        check_trees(cfg)
    db = Database(cfg.database)
    projects = Projects(cfg, db)
    executor = RunnerClient(cfg.runner_socket) if cfg.executor == "runner" else LocalExecutor(cfg)
    worker = AnalysisWorker(cfg, db, projects, executor=executor if cfg.executor == "runner" else None)
    telegram = Telegram(cfg)
    notifier = Notifier(cfg, telegram)
    worker.notifier = notifier
    jobs = Jobs(cfg, db, projects, executor, notifier,
                ready=lambda pid: overview.ready(projects, worker, pid))
    telegram.on_answer = lambda pid, domain, allow: jobs.answer_egress(pid, domain, allow)
    reviews = Reviews(cfg, db, projects)
    shares = Shares(db, cfg.data_dir / "share.key")
    claude_cache = {"t": 0.0, "info": None}

    def claude_info():
        """For the header: how Claude is reached and whether the token runs out soon (cached for a minute)."""
        import time as _time
        if _time.monotonic() - claude_cache["t"] < 60:
            return claude_cache["info"]
        info = {"kind": None, "warn": False, "missing": False, "expires": cfg.token_expires or None}
        status = {}
        try:
            if cfg.executor == "runner":
                status = executor.status()
                info["kind"] = status["kind"]
            else:
                info["kind"] = config_mod.claude_auth().kind
            info["missing"] = info["kind"] is None
        except config_mod.MissingAuth:
            info["missing"] = True
        except Exception:
            info["kind"] = None                       # the runner does not answer: say nothing rather than guess
        info["expires"] = cfg.token_expires or status.get("expires") or None
        if info["kind"] == "oauth" and info["expires"]:
            import datetime as _dt
            info["days"] = (_dt.date.fromisoformat(info["expires"]) - _dt.date.today()).days
            info["warn"] = info["days"] < 30
        claude_cache.update(t=_time.monotonic(), info=info)
        return info
    tus = TusApp(cfg, db, projects, on_stored=worker.request)
    templates = Jinja2Templates(directory=str(PKG / "templates"))
    templates.env.globals["icon"] = icon = lambda name: Markup(   # static/img/icons.svg, see static/js/icons.js
        f'<svg class="ico" aria-hidden="true"><use href="/static/img/icons.svg#{escape(name)}"></use></svg>')
    # "Done → Export" on a button: the words in spans, the arrows as icons (like arrowText in icons.js)
    templates.env.filters["arrows"] = lambda text: Markup(icon("arrow-right")).join(
        Markup("<span>%s</span>") % part for part in str(text).split(" → "))

    def lang_of(request: Request) -> str:
        return i18n.negotiate(request.cookies.get(i18n.COOKIE), request.headers.get("accept-language"))

    def page(request: Request, name: str, **ctx):
        lang = lang_of(request)
        ctx.setdefault("guest", (request.scope.get("state") or {}).get("guest"))
        ctx.update(lang=lang, languages=i18n.languages(), version=__version__,
                   t=lambda key, **v: i18n.translate(lang, key, **v), disks=disk_space(cfg),
                   claude=claude_info(), queue=jobs.running_count(),
                   cut_name=lambda c: i18n.translate(lang, "cuts.name", n=(re.findall(r"\d+", str(c)) or [c])[-1]),
                   queued=db.one("SELECT COUNT(*) AS n FROM jobs WHERE state='queued'")["n"])
        return templates.TemplateResponse(request, name, ctx)

    async def read_json(request: Request):
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_JSON:
                raise HTTPException(413, "request too large")
        try:
            return json.loads(body)
        except ValueError:
            raise HTTPException(400, "body is not JSON")

    async def read_form(request: Request) -> dict:
        """Small URL-encoded forms only (no file uploads go through forms)."""
        if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            raise HTTPException(415, "form expected")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 64 * 1024:
                raise HTTPException(413, "request too large")
        return {k: v[-1] for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace")).items()}

    def project_or_404(pid):
        try:
            return projects.get(pid)
        except (NotFound, ValueError):
            raise HTTPException(404, "unknown project")

    # ------------------------------------------------------------ pages

    async def home(request):
        cards = []
        for p in projects.list():
            st = await anyio.to_thread.run_sync(overview.state, projects, worker, jobs, reviews, projects.get(p["id"]))
            cards.append({"p": p, "st": st, "badge": overview.badge(st)})
        return page(request, "projects.html", cards=cards)

    async def new_project(request):
        if request.method == "POST":
            form = await read_form(request)
            parts = ([x for x in cutter_project.ANALYSIS_PARTS if form.get(f"part_{x}")]
                     if form.get("parts_sent") else None)
            try:
                p = projects.create(str(form.get("name", "")), str(form.get("frame") or "") or None, parts)
            except ValueError:
                return page(request, "project_new.html", error="new.name_missing", frames=cutter_project.FRAMES,
                            parts=cutter_project.ANALYSIS_PARTS, chosen=parts or cutter_project.ANALYSIS_PARTS)
            return RedirectResponse(f"/projects/{p['id']}/setup", status_code=303)
        return page(request, "project_new.html", error=None, frames=cutter_project.FRAMES,
                    parts=cutter_project.ANALYSIS_PARTS, chosen=cutter_project.ANALYSIS_PARTS)

    async def export_page(request):
        p = project_or_404(request.path_params["pid"])
        cuts = await anyio.to_thread.run_sync(cutter_project.cuts, projects.paths(p["id"]))
        return page(request, "export.html", project=p, cuts=cuts, cut=cuts[-1] if cuts else None,
                    formats=export_mod.FORMATS)

    async def export_download(request):
        p = project_or_404(request.path_params["pid"])
        form = await read_form(request)
        lang = lang_of(request)
        paths = projects.paths(p["id"])
        cuts = await anyio.to_thread.run_sync(cutter_project.cuts, paths)
        try:
            req = export_mod.check_request(form, cuts)
            zip_path = await anyio.to_thread.run_sync(export_mod.build, cfg, paths, p["name"], req, lang)
        except export_mod.ExportError as e:
            msg = i18n.translate(lang, e.key, message=str(e))
            return JSONResponse({"error": str(e), "message": msg}, status_code=422)
        name = f'{export_mod.safe_name(p["name"])}_{req["cut"]}_{req["format"]}.zip'
        return FileResponse(zip_path, media_type="application/zip", filename=name,
                            background=BackgroundTask(zip_path.unlink, missing_ok=True))

    async def api_manifest(request):
        p = project_or_404(request.path_params["pid"])
        uploaded = {c["id"] for c in projects.clips(p["id"]) if c["state"] == "uploaded"}
        try:
            data = json.loads(projects.paths(p["id"]).manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"clips": []}
        return JSONResponse({"folder": p["footage_folder"], "clips": [c for c in data["clips"] if c["id"] in uploaded]})

    async def api_folder(request):
        p = project_or_404(request.path_params["pid"])
        data = await read_json(request)
        name = str(data.get("name") or "").strip()[:255]
        if not name or "/" in name or "\\" in name:
            raise HTTPException(422, "folder name expected")
        await anyio.to_thread.run_sync(projects.set_folder, p["id"], name)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ briefing and jobs

    async def api_briefing(request):
        p = project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        if request.method == "PUT":
            body = await read_json(request)
            try:
                data = briefing_mod.check(body)
                if body.get("frame"):
                    cutter_project.set_frame(paths, str(body["frame"]))
            except (briefing_mod.BriefingError, ValueError) as e:
                return JSONResponse({"error": str(e)}, status_code=422)
            lang = lang_of(request)
            briefing_mod.save(paths, data, lambda key, **v: i18n.translate(lang, key, **v), p["name"])
            egress.record(db, p["id"], "briefing", "\n".join(str(v) for v in data.values()), replace=True)
            return JSONResponse({"ok": True})
        return JSONResponse(dict(briefing_mod.load(paths), frame=cutter_project.frame(paths)))

    async def api_memory(request):
        """The project memory (CLAUDE.md in the project folder): read, or replaced by the user."""
        p = project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        if request.method == "PUT":
            if jobs.active(p["id"]) and jobs.active(p["id"])["state"] == "running":
                return JSONResponse({"error": "a job is running", "message":
                                     i18n.translate(lang_of(request), "memory.error.running")}, status_code=409)
            try:
                await anyio.to_thread.run_sync(memory.write_memory, paths, (await read_json(request)).get("text"))
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=422)
        return JSONResponse({"text": memory.read_memory(paths)})

    async def api_preferences(request):
        """The user's preferences for all projects (given to every job)."""
        if request.method == "PUT":
            try:
                text = (await read_json(request)).get("text")
                await anyio.to_thread.run_sync(memory.write_preferences, cfg, text)
                egress.record(db, egress.ALL, "preferences", text, replace=True)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=422)
        return JSONResponse({"text": memory.read_preferences(cfg)})

    async def api_egress(request):
        """Domains the project's agent may reach besides the fixed list, its questions, and the user's answers."""
        p = project_or_404(request.path_params["pid"])
        if request.method == "POST":
            data = await read_json(request)
            domain = str(data.get("domain") or "").lower()
            if egress.registrable(domain) != domain:
                return JSONResponse({"error": "not a domain"}, status_code=422)
            await anyio.to_thread.run_sync(jobs.answer_egress, p["id"], domain, bool(data.get("allow")))
        return JSONResponse(egress.overview(db, p["id"]))

    async def api_telegram(request):
        """Settings: the bot token (POST token / DELETE), the link of the user's account (POST link / unlink), a
        test message (POST test)."""
        try:
            if request.method == "DELETE":
                telegram.clear()
            elif request.method == "POST":
                data = await read_json(request)
                action = data.get("action")
                if action == "token":
                    await anyio.to_thread.run_sync(telegram.set_token, str(data.get("token") or ""))
                elif action == "link":
                    url = await anyio.to_thread.run_sync(telegram.link_code, lang_of(request))
                    return JSONResponse({**telegram.status(), "link": url})
                elif action == "unlink":
                    telegram.unlink()
                elif action == "test":
                    await anyio.to_thread.run_sync(telegram.test)
                else:
                    return JSONResponse({"error": "unknown action"}, status_code=400)
        except TelegramError as e:
            return JSONResponse({"error": str(e), **telegram.status()}, status_code=400)
        return JSONResponse(telegram.status())

    def job_json(j):
        return {k: j[k] for k in ("id", "kind", "cut", "state", "model", "attempt", "reason", "message", "resume_after",
                                  "cost_usd", "turns", "created", "started", "ended", "note")}

    def job_or_404(jid):
        try:
            return jobs.get(jid)
        except LookupError:
            raise HTTPException(404, "unknown job")

    def job_error(request, e):
        return JSONResponse({"error": str(e), "message": i18n.translate(lang_of(request), e.key)}, status_code=409)

    async def api_jobs(request):
        p = project_or_404(request.path_params["pid"])
        if request.method == "POST":
            data = await read_json(request)
            try:
                j = await anyio.to_thread.run_sync(jobs.create, p["id"], str(data.get("kind", "draft")),
                                                   str(data.get("note") or ""))
            except JobError as e:
                return job_error(request, e)
            return JSONResponse(job_json(j), status_code=201)
        return JSONResponse({"jobs": [job_json(j) for j in jobs.for_project(p["id"])],
                             "cuts": cutter_project.cuts(projects.paths(p["id"]))})

    async def api_job_action(request):
        j = job_or_404(request.path_params["jid"])
        action = request.path_params["action"]
        try:
            if action == "resume":
                j = await anyio.to_thread.run_sync(jobs.resume, j["id"])
            elif action == "cancel":
                j = await anyio.to_thread.run_sync(jobs.cancel, j["id"])
            else:
                raise HTTPException(404)
        except JobError as e:
            return job_error(request, e)
        return JSONResponse(job_json(j))

    async def api_job_events(request):
        """Server-sent events: the job's log as it grows, then its state; ends when the job is no longer active."""
        j = job_or_404(request.path_params["jid"])
        if request.path_params.get("pid") not in (None, j["project_id"]):
            raise HTTPException(404, "unknown job")
        after = int(request.query_params.get("after", "0") or 0)

        async def gen():
            nonlocal after
            while True:
                for ev in await anyio.to_thread.run_sync(jobs.events, j["id"], after):
                    after = ev["n"]
                    yield f"id: {ev['n']}\nevent: log\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                cur = jobs.get(j["id"])
                yield f"event: job\ndata: {json.dumps(job_json(cur))}\n\n"
                if cur["state"] not in ("queued", "running") or await request.is_disconnected():
                    return
                await anyio.sleep(1)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    async def api_job_raw(request):
        j = job_or_404(request.path_params["jid"])
        f = jobs_dir(j) / f"attempt-{j['attempt']}.jsonl"
        if not f.is_file():
            raise HTTPException(404, "no raw log")
        return FileResponse(f, media_type="application/x-ndjson", filename=f"{j['cut']}-attempt-{j['attempt']}.jsonl")

    def jobs_dir(j):
        from .jobs import job_dir
        return job_dir(projects.paths(j["project_id"]), j["id"])

    async def api_claude(request):
        """Which Claude authentication is used and whether the subscription token runs out soon."""
        info = {"executor": cfg.executor, "kind": None, "token_expires": cfg.token_expires or None, "warn": False}
        try:
            if cfg.executor == "runner":
                info.update(await anyio.to_thread.run_sync(executor.status))
            else:
                info["kind"] = config_mod.claude_auth().kind
        except (RunnerError, config_mod.ConfigError) as e:
            info["error"] = str(e)
        if info.get("kind") == "oauth" and cfg.token_expires:
            import datetime
            days = (datetime.date.fromisoformat(cfg.token_expires) - datetime.date.today()).days
            info.update(days_left=days, warn=days < 30)
        return JSONResponse(info)

    async def settings_page(request):
        info = {"kind": None, "source": None, "can_store": False}
        if cfg.executor == "runner":
            try:
                info.update(await anyio.to_thread.run_sync(executor.status))
            except RunnerError as e:
                info["error"] = str(e)
        else:
            info["error"] = "runner"
        return page(request, "settings.html", nav="settings", access=info,
                    expires=cfg.token_expires or info.get("expires"))

    async def api_claude_token(request):
        """Enter or remove the subscription token. It goes straight to the runner and is never sent back."""
        if cfg.executor != "runner":
            raise HTTPException(409, "tokens can be entered only with the runner service")
        try:
            if request.method == "DELETE":
                await anyio.to_thread.run_sync(executor.clear_token)
            else:
                data = await read_json(request)
                await anyio.to_thread.run_sync(executor.set_token, str(data.get("token", "")))
        except RunnerError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        claude_cache["t"] = float("-inf")                  # the header shows the new state at once
        log.info("Claude token %s in the web UI", "removed" if request.method == "DELETE" else "entered")
        return JSONResponse({"ok": True})

    async def delete_project(request):
        p = project_or_404(request.path_params["pid"])
        form = await read_form(request)
        if form.get("confirm") != p["name"]:
            st = await anyio.to_thread.run_sync(overview.state, projects, worker, jobs, reviews, p)
            return page(request, "setup.html", project=p, st=st, delete_error=True, frames=cutter_project.FRAMES,
                        parts=cutter_project.ANALYSIS_PARTS)
        await anyio.to_thread.run_sync(jobs.cancel_project, p["id"])
        await anyio.to_thread.run_sync(reviews.forget, p["id"])
        await anyio.to_thread.run_sync(worker.cancel, p["id"])
        await anyio.to_thread.run_sync(projects.delete, p["id"])
        log.info("project %s deleted", p["id"])
        return RedirectResponse("/", status_code=303)

    async def set_language(request):
        code = request.path_params["code"]
        nxt = request.query_params.get("next", "/")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        resp = RedirectResponse(nxt, status_code=303)
        if code in i18n.catalogs():
            resp.set_cookie(i18n.COOKIE, code, max_age=365 * 86400, samesite="lax", httponly=False)
        return resp

    async def catalog(request):
        code = request.path_params["code"]
        if code not in i18n.catalogs():
            raise HTTPException(404)
        return JSONResponse(i18n.catalogs()[code], headers={"cache-control": "no-cache"})

    # ------------------------------------------------------------ API

    async def api_clips(request):
        p = project_or_404(request.path_params["pid"])
        if request.method == "POST":
            data = await read_json(request)
            try:
                status = await anyio.to_thread.run_sync(projects.register, p["id"], data)
            except SidecarError as e:
                return JSONResponse({"error": f"sidecar: {e}"}, status_code=422)
            return JSONResponse(status)
        return JSONResponse({"project": p, "clips": [
            {k: c[k] for k in ("id", "rel_path", "size", "duration", "state", "reason", "standin_size", "gpmd", "srt")}
            for c in projects.clips(p["id"])]})

    async def api_telemetry(request):
        p = project_or_404(request.path_params["pid"])
        kind = request.path_params["kind"]
        if kind not in ("gpmd", "srt"):
            raise HTTPException(404)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > 256 * 1024 * 1024:
                raise HTTPException(413, "file too large")
        try:
            await anyio.to_thread.run_sync(projects.store_telemetry, p["id"], request.path_params["cid"], kind,
                                           bytes(body))
        except NotFound:
            raise HTTPException(404, "unknown clip")
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        return JSONResponse({"ok": True})

    async def api_analysis(request):
        p = project_or_404(request.path_params["pid"])
        done = await anyio.to_thread.run_sync(cutter_project.progress, projects.paths(p["id"]))
        done.pop("fingerprints")
        uploaded = sum(1 for c in projects.clips(p["id"]) if c["state"] == "uploaded")
        footage = await anyio.to_thread.run_sync(projects.footage_summary, p["id"])
        remaining = await anyio.to_thread.run_sync(eta.remaining, p, projects.paths(p["id"]), cfg.work_dir)
        return JSONResponse({"state": p["analysis_state"], "message": p["analysis_message"], "updated": p["analysis_updated"],
                             "uploaded": uploaded, **done, "footage": footage, "remaining_s": remaining})

    async def api_analysis_stop(request):
        p = project_or_404(request.path_params["pid"])
        await anyio.to_thread.run_sync(worker.stop, p["id"])
        return JSONResponse({"ok": True})

    async def api_clips_delete(request):
        """DELETE /clips (all) or /clips/{cid}. Not while a cut is being made: the agent works with the footage."""
        p = project_or_404(request.path_params["pid"])
        if jobs.active(p["id"]):
            return JSONResponse({"error": "a cut is being made; cancel it first"}, status_code=409)
        cid = request.path_params.get("cid")
        await anyio.to_thread.run_sync(worker.cancel, p["id"])
        n = await anyio.to_thread.run_sync(projects.delete_clips, p["id"], [cid] if cid else None)
        if cid and not n:
            return JSONResponse({"error": "unknown clip"}, status_code=404)
        if await anyio.to_thread.run_sync(projects.clips, p["id"]):
            worker.request(p["id"])                  # clips.json without the deleted clips
        else:
            await anyio.to_thread.run_sync(worker.db_state_idle, p["id"])
        log.info("project %s: %d clip(s) deleted", p["id"], n)
        return JSONResponse({"deleted": n})

    async def api_analysis_parts(request):
        """GET/PUT the parts of the analysis; a change analyses the project again (cutter adds what is missing)."""
        p = project_or_404(request.path_params["pid"])
        paths = projects.paths(p["id"])
        if request.method == "PUT":
            body = await read_json(request)
            try:
                changed = await anyio.to_thread.run_sync(cutter_project.set_analysis_parts, paths, body.get("parts"))
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=422)
            await anyio.to_thread.run_sync(cutter_project.write, paths, p["name"])
            if changed and await anyio.to_thread.run_sync(projects.clips, p["id"]):
                worker.request(p["id"])
            return JSONResponse({"parts": cutter_project.analysis_parts(paths), "changed": changed})
        return JSONResponse({"parts": cutter_project.analysis_parts(paths), "all": list(cutter_project.ANALYSIS_PARTS)})

    async def api_analysis_start(request):
        p = project_or_404(request.path_params["pid"])
        worker.request(p["id"])
        return JSONResponse({"ok": True})

    async def api_status(request):
        return JSONResponse({"version": __version__, "disks": [d.__dict__ for d in disk_space(cfg)]})

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def http_error(request, exc):
        if request.url.path.startswith("/api/") or "/review/api/" in request.url.path:
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        if "text/html" in request.headers.get("accept", ""):
            known = exc.status_code in (404, 500)
            r = page(request, "error.html", status=exc.status_code,
                     title_key=f"error.title_{exc.status_code}" if known else "error.title",
                     text_key=f"error.text_{exc.status_code}" if known else "error.text")
            r.status_code = exc.status_code
            return r
        return Response(f"{exc.status_code} {exc.detail}\n", status_code=exc.status_code, media_type="text/plain")

    async def server_error(request, exc):
        log.exception("request failed: %s", request.url.path)
        if request.url.path.startswith("/api/") or "text/html" not in request.headers.get("accept", ""):
            return JSONResponse({"error": "internal error"}, status_code=500)
        r = page(request, "error.html", status=500, title_key="error.title_500", text_key="error.text_500")
        r.status_code = 500
        return r

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def housekeeping():
            while True:
                n = await anyio.to_thread.run_sync(tus.expire)
                if n:
                    log.info("removed %d expired uploads", n)
                await anyio.sleep(3600)

        async with anyio.create_task_group() as tg:
            tg.start_soon(housekeeping)
            await anyio.to_thread.run_sync(jobs.start)
            telegram.start()
            n = await anyio.to_thread.run_sync(worker.resume)
            if n:
                log.info("analysis resumes for %d projects", n)
            yield
            tg.cancel_scope.cancel()
            await anyio.to_thread.run_sync(worker.close)
            await anyio.to_thread.run_sync(jobs.close)
            telegram.close()

    routes = [
        Route("/", home),
        Route("/projects/new", new_project, methods=["GET", "POST"]),
        Route("/projects/{pid}/delete", delete_project, methods=["POST"]),
        Route("/projects/{pid}/export", export_page),
        Route("/projects/{pid}/export", export_download, methods=["POST"]),
        Route("/lang/{code}", set_language),
        Route("/i18n/{code}.json", catalog),
        Route("/api/status", api_status),
        Route("/api/projects/{pid}/analysis", api_analysis),
        Route("/api/projects/{pid}/analysis", api_analysis_start, methods=["POST"]),
        Route("/api/projects/{pid}/analysis/parts", api_analysis_parts, methods=["GET", "PUT"]),
        Route("/api/projects/{pid}/clips", api_clips, methods=["GET", "POST"]),
        Route("/api/projects/{pid}/clips", api_clips_delete, methods=["DELETE"]),
        Route("/api/projects/{pid}/clips/{cid}", api_clips_delete, methods=["DELETE"]),
        Route("/api/projects/{pid}/analysis/stop", api_analysis_stop, methods=["POST"]),
        Route("/api/projects/{pid}/manifest", api_manifest),
        Route("/api/projects/{pid}/briefing", api_briefing, methods=["GET", "PUT"]),
        Route("/api/projects/{pid}/memory", api_memory, methods=["GET", "PUT"]),
        Route("/api/preferences", api_preferences, methods=["GET", "PUT"]),
        Route("/api/projects/{pid}/egress", api_egress, methods=["GET", "POST"]),
        Route("/api/telegram", api_telegram, methods=["GET", "POST", "DELETE"]),
        Route("/api/projects/{pid}/jobs", api_jobs, methods=["GET", "POST"]),
        Route("/api/jobs/{jid}/events", api_job_events),
        Route("/api/projects/{pid}/jobs/{jid}/events", api_job_events),      # the same, also for a guest
        Route("/api/jobs/{jid}/raw", api_job_raw),
        Route("/api/jobs/{jid}/{action}", api_job_action, methods=["POST"]),
        Route("/api/claude", api_claude),
        Route("/api/claude/token", api_claude_token, methods=["PUT", "DELETE"]),
        Route("/settings", settings_page),
        Route("/api/projects/{pid}/folder", api_folder, methods=["POST"]),
        Route("/api/projects/{pid}/clips/{cid}/telemetry/{kind}", api_telemetry, methods=["PUT"]),
        Route("/healthz", healthz),
        *tus.routes(),
        *pages.routes(pages.Context(projects=projects, jobs=jobs, reviews=reviews, worker=worker, cfg=cfg, page=page,
                                    shares=shares,
                                    read_json=read_json, project_or_404=project_or_404, lang_of=lang_of)),
        Mount("/static", app=StaticFiles(directory=str(PKG / "static")), name="static"),
    ]
    middleware = [
        Middleware(SecurityHeaders),
        Middleware(BasicAuthMiddleware, htpasswd=cfg.htpasswd, trusted_proxies=cfg.trusted_proxies,
                   health_token=health_token, shares=shares),
    ]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan,
                    exception_handlers={HTTPException: http_error, Exception: server_error})
    app.state.cfg, app.state.db, app.state.projects, app.state.tus, app.state.worker = cfg, db, projects, tus, worker
    app.state.jobs, app.state.reviews, app.state.telegram = jobs, reviews, telegram
    app.state.shares = shares
    return app
