"""HTTP Basic Auth for the whole app, with cutter's review-app mechanism.

The password hash (scrypt) lives in DATA_DIR/htpasswd, written by `cutlery set-password`. After
MAX_FAILURES failed logins from one IP within LOCK_S seconds, that IP gets 429 until the window has passed.
Every failure is logged as "cutlery: login failed from <ip>" (see examples/fail2ban).

The only request that passes without a password is the container's own health check: it comes from the
loopback interface and carries a random token that only processes inside the container can read.

Guests (share.py) come in without the password: /s/<token> sets their cookie, and a request with that cookie and no
Basic Auth may use the paths share.guest_may() allows for the shared project; scope["state"]["guest"] says who.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import pathlib
import secrets

import anyio

from . import share
from cutter.review_app import server as review_server
from cutter.review_app import set_password

log = logging.getLogger("cutlery.auth")

MAX_FAILURES, LOCK_S = review_server.MAX_FAILURES, review_server.LOCK_S
REALM = 'Basic realm="Cutlery", charset="UTF-8"'
HEALTH_HEADER = b"x-cutlery-health"
HEALTH_TOKEN_FILE = pathlib.Path("/tmp/cutlery-health-token")     # inside the container, not in a mounted tree
PasswordError = set_password.PasswordError


def set_single_user(htpasswd: pathlib.Path, name: str, generate: bool = False):
    """Make NAME the instance's only user. Returns (result, text) like cutter's set_password.run."""
    set_password.check_name(name)
    for other in set_password.names(str(htpasswd)):
        if other != name:
            set_password.store(str(htpasswd), other)
    pw = set_password.generate() if generate else set_password.ask(name)
    set_password.store(str(htpasswd), name, pw)
    text = f"Login for Cutlery set: user {name} (only the password's hash is stored)."
    if generate:
        text += f"\n  user:     {name}\n  password: {pw}\nIt is shown only this once."
    return {"name": name, "generated": generate}, text


def write_health_token() -> str:
    token = secrets.token_urlsafe(24)
    fd = os.open(HEALTH_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


class BasicAuthMiddleware:
    def __init__(self, app, htpasswd: pathlib.Path, trusted_proxies=(), health_token: str | None = None, shares=None):
        self.app = app
        self.shares = shares
        self.auth = review_server.Auth(str(htpasswd))
        self.trusted = tuple(trusted_proxies)
        self.health_token = health_token.encode() if health_token else None

    def client_ip(self, scope) -> str:
        peer = (scope.get("client") or ("", 0))[0]
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return peer or "?"
        if any(peer_ip in net for net in self.trusted):
            headers = dict(scope.get("headers") or [])
            real = headers.get(b"x-real-ip") or headers.get(b"x-forwarded-for", b"").split(b",")[-1]
            real = real.strip().decode("latin-1")
            try:
                return str(ipaddress.ip_address(real))
            except ValueError:
                pass
        return str(peer_ip)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        peer = (scope.get("client") or ("", 0))[0]
        if (self.health_token and scope["path"] == "/healthz" and peer in ("127.0.0.1", "::1")
                and secrets.compare_digest(headers.get(HEALTH_HEADER, b""), self.health_token)):
            return await self.app(scope, receive, send)

        ip = self.client_ip(scope)
        if self.auth.locked(ip):
            return await _plain(send, 429, "Too many failed logins. Try again later.\n",
                                [(b"retry-after", str(LOCK_S).encode())])
        if not await anyio.to_thread.run_sync(self.auth.has_users):
            return await _plain(send, 503, "No password is set. Run: docker compose run --rm --no-deps cutlery set-password\n")
        header = headers.get(b"authorization", b"").decode("latin-1")
        if self.shares is not None and not header:
            if scope["path"].startswith("/s/"):
                return await self.open_link(scope, send, ip, headers)
            token = _cookie(headers, share.COOKIE)
            if token:
                return await self.as_guest(scope, receive, send, ip, headers, token)
        user = await anyio.to_thread.run_sync(self.auth.basic, header) if header else None
        if user is None:
            if header:
                self.auth.failure(ip)
                log.warning("cutlery: login failed from %s", ip)
            return await _plain(send, 401, "Login required.\n", [(b"www-authenticate", REALM.encode())])
        scope.setdefault("state", {})["user"] = user
        return await self.app(scope, receive, send)


    # ------------------------------------------------------------ guests

    async def open_link(self, scope, send, ip, headers):
        token = scope["path"][3:]
        guest = await anyio.to_thread.run_sync(self.shares.check, token)
        if not guest:
            self.auth.failure(ip)
            log.warning("cutlery: unknown share link from %s", ip)
            return await _plain(send, 404, "This link is not valid (any more). Ask for a new one.\n",
                                [(b"set-cookie", _set_cookie("", 0, headers))])
        log.info("share %s (%s) opened from %s", guest["id"], guest["name"], ip)
        return await _redirect(send, f"/projects/{guest['project_id']}/review",
                               [(b"set-cookie", _set_cookie(token, 400 * 86400, headers))])

    async def as_guest(self, scope, receive, send, ip, headers, token):
        guest = await anyio.to_thread.run_sync(self.shares.check, token)
        if not guest:
            return await _plain(send, 403, "This link has expired or was withdrawn. Ask for a new one.\n",
                                [(b"set-cookie", _set_cookie("", 0, headers))])
        method, path, home = scope["method"], scope["path"], f"/projects/{guest['project_id']}/review"
        if not share.guest_may(method, path, guest["project_id"]):
            if method == "GET" and b"text/html" in headers.get(b"accept", b""):
                return await _redirect(send, home)              # the start page, a tab of the owner's: the review
            return await _plain(send, 403, "Not allowed with a shared link.\n")
        state = scope.setdefault("state", {})
        state["user"], state["guest"] = guest["name"], guest
        return await self.app(scope, receive, send)


def _cookie(headers, name: str) -> str | None:
    for part in headers.get(b"cookie", b"").decode("latin-1").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def _set_cookie(value: str, max_age: int, headers) -> bytes:
    secure = headers.get(b"x-forwarded-proto", b"").split(b",")[0].strip() == b"https"
    return (f"{share.COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else "")).encode("latin-1")


async def _redirect(send, location: str, extra=()):
    await send({"type": "http.response.start", "status": 303,
                "headers": [(b"location", location.encode()), (b"content-length", b"0"), (b"cache-control", b"no-store"),
                            *extra]})
    await send({"type": "http.response.body", "body": b""})


async def _plain(send, status, text, extra=()):
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store"),
                            *extra]})
    await send({"type": "http.response.body", "body": body})
