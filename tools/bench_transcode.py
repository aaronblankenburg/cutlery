"""Measure the browser transcoding with 1, 2, 3 … pipeline workers in parallel, on real footage.

    .venv/bin/python tools/bench_transcode.py FOLDER [--workers 1,2,3,4] [--headless]

Starts Cutlery on 127.0.0.1 with temporary storage, opens Google Chrome (with a window by default: headless Chrome
has no GPU and would measure software decoding only), and transcodes every video in FOLDER with the real
pipeline worker (static/js/pipeline-worker.js), N at a time. Prints wall time and speed (seconds of footage per
second) per N, as a Markdown table.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
VIDEO = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".mkv", ".avi", ".mxf", ".webm", ".3gp", ".mpg"}

JS = r"""
async ({ n, names }) => {
  const input = document.getElementById('bench-input');
  const files = [...input.files].filter((f) => names.includes(f.webkitRelativePath));
  const workers = Array.from({ length: n }, () => new Worker('/static/js/pipeline-worker.js', { type: 'module' }));
  let next = 1;
  const call = (w, type, payload) => new Promise((resolve, reject) => {
    const id = next++;
    const onmsg = ({ data }) => {
      if (data.id !== id || 'progress' in data) return;
      w.removeEventListener('message', onmsg);
      data.ok ? resolve(data.result) : reject(new Error(data.error));
    };
    w.addEventListener('message', onmsg);
    w.postMessage({ id, type, ...payload });
  });
  await Promise.all(workers.map((w) => call(w, 'capabilities', {})));
  const queue = [...files].sort((a, b) => b.size - a.size);      // largest first: less idle time at the end
  const done = [];
  const t0 = performance.now();
  await Promise.all(workers.map(async (w, k) => {
    while (queue.length) {
      const f = queue.shift();
      const t = performance.now();
      const out = `bench-${n}-${k}-${done.length}.mp4`;
      await call(w, 'transcode', { file: f, outName: out });
      done.push({ file: f.webkitRelativePath, secs: (performance.now() - t) / 1000 });
      await call(w, 'remove', { name: out });
    }
  }));
  const wall = (performance.now() - t0) / 1000;
  workers.forEach((w) => w.terminate());
  return { wall, done };
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return float(json.loads(r.stdout or "{}").get("format", {}).get("duration") or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--workers", default="1,2,3,4")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    folder = pathlib.Path(a.folder).resolve()
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO)
    names = [f"{folder.name}/{p.relative_to(folder).as_posix()}" for p in files]
    footage = sum(duration(p) for p in files)
    size = sum(p.stat().st_size for p in files)

    import uvicorn
    from cutter.review_app import set_password
    from playwright.sync_api import sync_playwright

    from cutlery import config as config_mod
    from cutlery.app import create_app

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cutlery-bench-"))
    for d in ("data", "work", "models"):
        (tmp / d).mkdir()
    cfg = config_mod.load({"CUTLERY_DATA_DIR": str(tmp / "data"), "CUTLERY_WORK_DIR": str(tmp / "work"),
                           "CUTLERY_MODELS_DIR": str(tmp / "models")})
    password = secrets.token_urlsafe(12)
    set_password.store(str(cfg.htpasswd), "cutlery", password)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)

    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=a.headless)
        page = b.new_context(http_credentials={"username": "cutlery", "password": password}).new_page()
        page.goto(f"http://127.0.0.1:{port}/")
        gpu = page.evaluate("(() => { const c = document.createElement('canvas').getContext('webgl');"
                            " const d = c && c.getExtension('WEBGL_debug_renderer_info');"
                            " return d ? c.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'no webgl'; })()")
        page.evaluate("() => { const i = document.createElement('input'); i.type = 'file'; i.id = 'bench-input';"
                      " i.webkitdirectory = true; i.multiple = true; document.body.append(i); }")
        page.set_input_files("#bench-input", str(folder))
        for n in (int(x) for x in a.workers.split(",")):
            for _ in range(a.repeat):
                r = page.evaluate(JS, {"n": n, "names": names})
                rows.append((n, r["wall"]))
                print(f"{n} worker(s): {r['wall']:.1f} s, {footage / r['wall']:.2f} x real time", flush=True)
        b.close()
    srv.should_exit = True

    print(f"\n{len(files)} clips, {footage / 60:.1f} min of footage, {size / 1e9:.2f} GB; {gpu}; "
          f"Chrome {'headless' if a.headless else 'with a window'}\n")
    print("| Workers | Wall time | Speed (× real time) | vs. 1 worker |\n|---|---|---|---|")
    base = min(w for n, w in rows if n == rows[0][0])
    for n in sorted({n for n, _ in rows}):
        w = min(x for m, x in rows if m == n)
        print(f"| {n} | {w:.1f} s | {footage / w:.2f} | {base / w:.2f} × |")


if __name__ == "__main__":
    main()
