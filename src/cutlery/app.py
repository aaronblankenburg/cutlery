"""The web application: pages, JSON API, uploads. Everything behind Basic Auth (see auth.py)."""
from __future__ import annotations

import contextlib
import importlib.resources
import json
import logging
import urllib.parse

import anyio
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import __version__, i18n
from . import config as config_mod
from .auth import BasicAuthMiddleware
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
    tus = TusApp(cfg, db, projects)
    templates = Jinja2Templates(directory=str(PKG / "templates"))

    def lang_of(request: Request) -> str:
        return i18n.negotiate(request.cookies.get(i18n.COOKIE), request.headers.get("accept-language"))

    def page(request: Request, name: str, **ctx):
        lang = lang_of(request)
        ctx.update(lang=lang, languages=i18n.languages(), version=__version__,
                   t=lambda key, **v: i18n.translate(lang, key, **v), disks=disk_space(cfg))
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
        return page(request, "projects.html", projects=projects.list())

    async def new_project(request):
        if request.method == "POST":
            form = await read_form(request)
            try:
                p = projects.create(str(form.get("name", "")))
            except ValueError:
                return page(request, "project_new.html", error="new.name_missing")
            return RedirectResponse(f"/projects/{p['id']}/footage", status_code=303)
        return page(request, "project_new.html", error=None)

    async def footage(request):
        p = project_or_404(request.path_params["pid"])
        return page(request, "footage.html", project=p)

    async def delete_project(request):
        p = project_or_404(request.path_params["pid"])
        form = await read_form(request)
        if form.get("confirm") != p["name"]:
            return page(request, "footage.html", project=p, delete_error=True)
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

    async def api_status(request):
        return JSONResponse({"version": __version__, "disks": [d.__dict__ for d in disk_space(cfg)]})

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def http_error(request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return Response(f"{exc.status_code} {exc.detail}\n", status_code=exc.status_code, media_type="text/plain")

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
            yield
            tg.cancel_scope.cancel()

    routes = [
        Route("/", home),
        Route("/projects/new", new_project, methods=["GET", "POST"]),
        Route("/projects/{pid}/footage", footage),
        Route("/projects/{pid}/delete", delete_project, methods=["POST"]),
        Route("/lang/{code}", set_language),
        Route("/i18n/{code}.json", catalog),
        Route("/api/status", api_status),
        Route("/api/projects/{pid}/clips", api_clips, methods=["GET", "POST"]),
        Route("/api/projects/{pid}/clips/{cid}/telemetry/{kind}", api_telemetry, methods=["PUT"]),
        Route("/healthz", healthz),
        *tus.routes(),
        Mount("/static", app=StaticFiles(directory=str(PKG / "static")), name="static"),
    ]
    middleware = [
        Middleware(SecurityHeaders),
        Middleware(BasicAuthMiddleware, htpasswd=cfg.htpasswd, trusted_proxies=cfg.trusted_proxies,
                   health_token=health_token),
    ]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan,
                    exception_handlers={HTTPException: http_error})
    app.state.cfg, app.state.db, app.state.projects, app.state.tus = cfg, db, projects, tus
    return app
