"""Command line: `cutlery serve`, `cutlery set-password`, `cutlery check`, `cutlery healthcheck`."""
from __future__ import annotations

import argparse
import logging
import sys
import urllib.request

from . import __version__
from . import config as config_mod


def _serve(args):
    import uvicorn

    from .app import create_app
    from .auth import write_health_token

    cfg = config_mod.load()
    app = create_app(cfg, health_token=write_health_token())
    uvicorn.run(app, host=args.host or cfg.host, port=args.port or cfg.port, proxy_headers=False,
                log_level="info", access_log=False, timeout_keep_alive=75)


def _runner(args):
    from . import runner

    runner.serve(config_mod.load())


def _set_password(args):
    from .auth import PasswordError, set_single_user

    cfg = config_mod.load()
    try:
        _, text = set_single_user(cfg.htpasswd, args.user, generate=args.generate)
    except PasswordError as e:
        sys.exit(str(e))
    print(text)


def _check(args):
    from .storage import check_trees

    cfg = config_mod.load()
    check_trees(cfg)
    print("Storage: data, work and models are writable.")
    if args.claude:
        auth = config_mod.claude_auth()
        print(f"Claude authentication: {auth.variable} is set.")


def _healthcheck(args):
    from .auth import HEALTH_HEADER, HEALTH_TOKEN_FILE

    cfg = config_mod.load()
    try:
        token = HEALTH_TOKEN_FILE.read_text().strip()
        req = urllib.request.Request(f"http://127.0.0.1:{cfg.port}/healthz", headers={HEALTH_HEADER.decode(): token})
        with urllib.request.urlopen(req, timeout=5) as r:
            sys.exit(0 if r.status == 200 else 1)
    except Exception as e:
        print(f"unhealthy: {e}", file=sys.stderr)
        sys.exit(1)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    ap = argparse.ArgumentParser(prog="cutlery", description="Cut films from your own footage in the browser.")
    ap.add_argument("--version", action="version", version=f"cutlery {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=_serve)
    p = sub.add_parser("runner", help="run the job runner (needs the Docker socket and the Claude credential)")
    p.set_defaults(func=_runner)
    p = sub.add_parser("set-password", help="set the login (there is exactly one user)")
    p.add_argument("--user", default="cutlery", help="user name for the login (default: cutlery)")
    p.add_argument("--generate", action="store_true", help="make a random password and print it once")
    p.set_defaults(func=_set_password)
    p = sub.add_parser("check", help="check storage (and with --claude the Claude authentication)")
    p.add_argument("--claude", action="store_true")
    p.set_defaults(func=_check)
    p = sub.add_parser("healthcheck", help="used by the container health check")
    p.set_defaults(func=_healthcheck)
    args = ap.parse_args(argv)
    try:
        args.func(args)
    except config_mod.ConfigError as e:
        sys.exit(f"cutlery: {e}")
