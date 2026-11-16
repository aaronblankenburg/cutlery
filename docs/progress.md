# Progress

Milestone log for the build described in PROMPT.md. Claude Code updates this file at the end of every
milestone (and before any model switch), so a fresh session can continue without prior context.

| Milestone | Status | Notes |
|---|---|---|
| M0 – plan, cutter / Cutlery split, architecture | done (2026-11-16) | Approved plan in docs/plan.md. Decisions: runner service holds the Docker socket; egress allowlist incl. FMA, ccMixter, Wikimedia Commons, Internet Archive, Jamendo. |
| M1 – browser transcoding, sidecar, upload | done (2026-11-16) | Server (Starlette, SQLite, Basic Auth via cutter's review-app auth), tus upload with server-side ffprobe check, browser pipeline (Mediabunny 1.64.0, createImageBitmap scaling, WASM AAC fallback), sidecar format (docs/sidecar.md), Dockerfile + compose.yaml with start check. Feasibility on real footage: docs/m1-feasibility.md. |
| M2 – analysis on 360p with sidecar metadata | done (2026-11-16) | cutter change (a) on cutter branch `cutlery-support` (commits 63ed483, b8fee48; all cutter tests green). Cutlery: cutter.toml per project, incremental analysis worker (debounced, restart-safe, status UI), `CUTLERY_ANALYSIS_ARGS`. Threshold comparison: docs/standin-analysis.md (no changes needed). Change (f) (review renders in WORK) moves to M5. |
| M3 – export with correct paths, round-trip test | next (Opus 5.5) | cutter changes (c) path mapping and (e) timeline fidelity. |
| M4 – Claude Code jobs, non-interactive skill mode | not started | |
| M5 – review buttons, GUI, English and German UI | not started | |
| M6 – FCPXML, Resolve and FilmCraft export | not started | |
| M7 – deployment, hardening, docs for self-hosters | not started | |

## State for the next session

- Dev setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`; run a dev server with
  `CUTLERY_DATA_DIR=… CUTLERY_WORK_DIR=… CUTLERY_MODELS_DIR=… .venv/bin/cutlery serve --port 8291` after
  `cutlery set-password`.
- Stand-ins: `WORK/projects/<id>/media/<rel_path>.mp4`; sidecars, `.gpmd`, `.srt`: `DATA/projects/<id>/sidecars/`;
  manifest: `DATA/projects/<id>/manifest.json`. Clip ids `c0001…` are never reused.
- cutter change (a) must read these sidecars (format in docs/sidecar.md; `video.rotation` is clockwise).
- Headless Chrome has no GPU: browser tests are slower than real use; never pass GPU flags to headless Chrome
  (transcoding hangs). Playwright `set_input_files` hangs on symlinked files; copy or hard-link test footage.
- The footage page is functional but not yet the full "Neu" design (briefing, storyboard, music follow in M5).
- Client-side failures (e.g. decoding errors) are shown in the browser but not yet stored on the server.

- cutter stand-in support is only on the branch. Build the base image with `docker build -t cutter:cutlery-dev ../cutter`
  and Cutlery with `--build-arg CUTTER_IMAGE=cutter:cutlery-dev`. Integration test:
  `CUTLERY_TEST_CUTTER_IMAGE=… CUTLERY_TEST_CUTTER_SRC=../cutter pytest -m integration tests/integration`.
- Edit-list clips (cut with stream copy): the stand-in may differ from the original by up to half a frame in
  timing and one frame in count (ffmpeg re-bases the first shown frame to 0, Mediabunny keeps the exact offset).
- Two byte-identical files in the footage folder have the same fingerprint and become one clip.
- The analysis runs inside the web container for now; M4 moves it into the runner's isolated container.
- Whisper (1.6 GB) is downloaded by the first real analysis run into `CUTLERY_MODELS_DIR`; not yet tested end to end.

## Open questions

- FilmCraft relink and FCPXML stabilisation support (M6).
- Face recognition at 360p (M2 measurement).
- HEVC, 4K phone/drone clips, real GoPro GPS tracks and DJI .SRT: none in the available real footage (mostly
  720p and 4K H.264). Needs test material and a run in Safari (Mac) or Chrome on Windows.
- WebKit browser tests need `sudo apt-get install libavif16` on the dev machine.
- Exact Jamendo/Internet Archive download hosts and Natural Earth host for the egress allowlist (M4).
