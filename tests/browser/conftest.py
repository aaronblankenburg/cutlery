"""Fixtures of the browser tests: a Cutlery server per test and a page in Chrome or WebKit."""
import secrets
import shutil
import socket
import threading
import time

import pytest

CHROME_ARGS: list[str] = []


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cfg):
    import uvicorn
    from cutter.review_app import set_password

    from cutlery.app import create_app

    password = secrets.token_urlsafe(12)
    set_password.store(str(cfg.htpasswd), "cutlery", password)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "password": password, "cfg": cfg, "app": srv.config.app}
    srv.should_exit = True
    thread.join(5)


def launch(p, name):
    if name == "chrome":
        if not shutil.which("google-chrome") and not shutil.which("google-chrome-stable"):
            pytest.skip("Google Chrome is not installed")
        return p.chromium.launch(channel="chrome", headless=True, args=CHROME_ARGS)
    try:
        return p.webkit.launch()
    except Exception as e:      # browser not installed
        pytest.skip(f"WebKit is not available: {e}")


@pytest.fixture(params=["chrome", "webkit"])
def page(request, server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = launch(p, request.param)
        ctx = browser.new_context(http_credentials={"username": "cutlery", "password": server["password"]},
                                  bypass_csp=False)
        pg = ctx.new_page()
        pg.browser_name = request.param
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        yield pg
        browser.close()
        assert not errors, errors




def new_project_api(page, server, name="Test"):
    """Create a project through the form and return its id (without waiting for the footage page's scripts)."""
    page.goto(server["url"] + "/projects/new")
    page.fill("#name", name)
    page.click("button[type=submit]")
    page.wait_for_url("**/footage")
    return page.url.split("/")[-2]
