# Progress

Milestone log for the build described in PROMPT.md. Claude Code updates this file at the end of every
milestone (and before any model switch), so a fresh session can continue without prior context.

| Milestone | Status | Notes |
|---|---|---|
| M0 – plan, cutter / Cutlery split, architecture | done (2026-11-16) | Approved plan in docs/plan.md. Decisions: runner service holds the Docker socket; egress allowlist incl. FMA, ccMixter, Wikimedia Commons, Internet Archive, Jamendo. |
| M1 – browser transcoding, sidecar, upload | done (2026-11-16) | Server (Starlette, SQLite, Basic Auth via cutter's review-app auth), tus upload with server-side ffprobe check, browser pipeline (Mediabunny 1.64.0, createImageBitmap scaling, WASM AAC fallback), sidecar format (docs/sidecar.md), Dockerfile + compose.yaml with start check. Feasibility on real footage: docs/m1-feasibility.md. |
| M2 – analysis on 360p with sidecar metadata | done (2026-11-16) | cutter change (a) on cutter branch `cutlery-support` (commits 63ed483, b8fee48; all cutter tests green). Cutlery: cutter.toml per project, incremental analysis worker (debounced, restart-safe, status UI), `CUTLERY_ANALYSIS_ARGS`. Threshold comparison: docs/standin-analysis.md (no changes needed). Change (f) (review renders in WORK) moves to M5. |
| M3 – export with correct paths, round-trip test | done (2026-11-16), round trip in Premiere pending | cutter (branch `cutlery-support`, fd2eb74): paths.py (c), export.py + fcp7.py + titles.py (e), `cutter export`, golden Premiere XML. Cutlery: export page (path, re-check of the originals by fingerprint with relink, formats, ZIP with localized IMPORT-PREMIERE.txt and licences), integration test with the real cutter. Manual check: docs/nle-roundtrip.md. |
| M4 – Claude Code jobs, non-interactive skill mode | done (2026-11-16) with a simulated claude; a run with a real Claude account is pending | cutter (main, 0.3.0): non-interactive skill mode, skill and docs in the image. Cutlery: briefing → briefing.md, job queue, `claude -p` with dontAsk + tool allowlist, stream-json → live log, pause on usage limit / auth with resume, webhook + MQTT, token expiry warning; runner service (Docker socket, isolated job containers, egress proxy with allowlist), analysis also through the runner. Tests: unit (fake claude), runner, stack test with Compose. |
| M5 – review buttons, GUI, English and German UI | done (2026-11-16, on Opus 5.5 with the user's approval) | Pages after docs/design: projects (cards with stills, paused banner), new project (footage, briefing, files with music licences, readiness checklist; a draft can be queued early and waits for upload and analysis), status (steps, live job log, pause/resume, analysis facts, people naming, cuts), review on cutter's review API (comments, agent changes and assumptions, lock + next cut, export), analysis view (contact sheets, transcripts), error pages, phone layout. cutter 0.3.1 (local commit, not pushed): paths.review_media, decision kind in review data. |
| M6 – FCPXML, Resolve and FilmCraft export | done (2026-11-16); round trips in the applications pending | cutter 0.4.0 (main ef866ce, not yet pushed): fcpxml.py (FCPXML 1.10, valid against Apple's DTD; stabilisation and noise reduction applied, captions, keyframes), `export --to fcp|resolve|filmcraft`, FilmCraft project built from an original-path timeline (offline on the server; round trip tested headless), stand-ins sized by their own picture in server timelines (fixes tiny review renders), FCP 7 file names no longer percent-encoded. Resolve gets FCPXML (decision in cutter docs/formats.md). Cutlery: all four formats on the export page, readme per application in en/de, tests, docs/nle-roundtrip.md. |
| M7 – deployment, hardening, docs for self-hosters | done (2026-11-17, on Opus 5.5 with the user's approval) | CI: check (unit tests) and image (ghcr.io/aaronblankenburg/cutlery: `main`, `sha-…`; on v* tags also version and `latest`). compose hardening (no capabilities and no-new-privileges for the web app, log rotation), examples/ for nginx, Caddy and fail2ban (filter tested with fail2ban-regex), README for self-hosters (install in four commands, external disk, proxy, backup rule, updates, troubleshooting), tests/unit/test_deploy.py. Without a Claude credential the runner starts and cuts wait (paused, no_credential). Claude token can be entered under Settings (stored by the runner in CUTLERY_SECRETS_DIR only). Deployed for the user (see below). |

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
- First run with a real Claude account (token or API key) – planned for after M4.
- Premiere round trip (docs/nle-roundtrip.md) has to be done on a Windows or Mac with Premiere.
- Whisper (1.6 GB) is downloaded by the first real analysis run into `CUTLERY_MODELS_DIR`; not yet tested end to end.

- Export: package folder names are English (`titles/`, `maps/`, `music/`) in every UI language, because the XML refers
  to them; the README and the licence file are localized. FCP 7 keyframe `<when>` is written relative to the media
  start (like in/out) – to be confirmed in the Premiere round trip. Stereo sound is one clipitem per clip.
- Export runs `cutter export` in the web container (like the analysis, until M4's runner).

- Jobs: tests/fixtures/fake_claude.py simulates claude (scenarios ok, usage_limit, auth, max_turns, crash, slow,
  probe). Stack test: build `cutlery:dev` (with `--build-arg CUTTER_IMAGE=cutter:main` until cutter 0.3.0 is
  published) and `cutlery:fakeclaude` (tests/integration/Dockerfile.fakeclaude), then
  `CUTLERY_TEST_STACK_IMAGE=cutlery:fakeclaude pytest -m integration tests/integration/test_stack.py`.
- Usage-limit reset times like "resets 3:45pm" are read in the server's time zone (TZ in .env).
- The runner runs as root inside its container (it holds the Docker socket, which is root-equivalent anyway).

- Project pages: /projects/<id> status, /setup footage+briefing+files, /review, /analysis, /export.
  The review API is cutter's own handler (review.py adapter); comments in DATA/.cutter/review/data/feedback.sqlite,
  renders in WORK/review/cuts (cutter ≥ 0.3.1) or next to the comments (0.3.0) – both are read.
- WebKit browser tests still need `sudo apt-get install libavif16`.
- Files opened in the Nextcloud app on a phone become read-only here (Nextcloud lock); `chmod u+w` is safe.

- M6: cutter 0.4.0 is committed on cutter main (ef866ce) but not pushed: SSH to GitHub was refused
  ("Permission denied (publickey)") although the key is in the agent. Cutlery's Dockerfile still pins cutter 0.3.0,
  which has no `--to fcp|resolve|filmcraft`: the export page offers them, so build with
  `--build-arg CUTTER_IMAGE=cutter:main` (local `docker build -t cutter:main ../cutter`) until 0.4.0 is published.
- FCPXML DTD check: save the DTD from Apple's "Document Type Definition" page (FCPXML reference) and set
  `CUTTER_FCPXML_DTD`; the cutter image has no lxml, install it into a mounted folder (`pip install --target`).
- The FilmCraft package has no title PNGs (FilmCraft makes titles natively); its rotation handling for portrait
  originals is untested (the server builds the project without the files).

- Deployment of the user's own instance (2026-11-17): server nextcloud@192.168.0.223, repo in ~/cutlery, `.env` there
  (mode 600) with the trees in /mnt/hdd/cutlery/{data,work,models,secrets}, CUTLERY_BIND=192.168.0.223:8080,
  image `ghcr.io/aaronblankenburg/cutlery:main` (no release tag yet). Reverse proxy: nginx on pi@192.168.0.222,
  /etc/nginx/sites-available/cutlery.aaronbb.com, Let's Encrypt via certbot; Pi-hole local DNS entry
  cutlery.aaronbb.com -> 192.168.0.222 (backup /etc/pihole/pihole.toml.bak-cutlery). Docker's root on .223 is on
  the small system disk (19 GB free after `docker image prune -a`); moving it to /mnt/hdd would be safer.
  The initial login is in ~/cutlery/.initial-login on the server (mode 600); delete it once noted.
- One unit-test run had one failure that did not repeat in two further runs (probably a timing test in test_jobs).

## Open questions

- Tag Cutlery v0.1.0 (then `latest` exists and the server can follow releases instead of `main`).

- Round trips in Final Cut Pro, DaVinci Resolve and FilmCraft (docs/nle-roundtrip.md), besides Premiere.
- Does Resolve keep volume/opacity keyframes and captions from FCPXML? If not, Resolve should get the FCP 7 XML.

- Face recognition at 360p (M2 measurement).
- HEVC, 4K phone/drone clips, real GoPro GPS tracks and DJI .SRT: none in the available real footage (mostly
  720p and 4K H.264). Needs test material and a run in Safari (Mac) or Chrome on Windows.
- WebKit browser tests need `sudo apt-get install libavif16` on the dev machine.
- Exact Jamendo/Internet Archive download hosts and Natural Earth host for the egress allowlist (M4).
