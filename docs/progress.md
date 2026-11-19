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
  image `ghcr.io/aaronblankenburg/cutlery:latest` (v0.1.0 tagged 2026-11-17). Reverse proxy: nginx on pi@192.168.0.222,
  /etc/nginx/sites-available/cutlery.aaronbb.com, Let's Encrypt via certbot; Pi-hole local DNS entry
  cutlery.aaronbb.com -> 192.168.0.222 (backup /etc/pihole/pihole.toml.bak-cutlery). Docker's root on .223 is on
  the small system disk (19 GB free after `docker image prune -a`); moving it to /mnt/hdd would be safer.
  The initial login is in ~/cutlery/.initial-login on the server (mode 600); the user wants it kept.
- 0.1.1 (2026-11-17): "Start upload again" button on the footage page (stops uploads and workers, goes through the
  folder again); a watchdog stops a worker that answers neither progress nor result (probe 2 min, transcode 5 min);
  uploadRow can no longer hang on a failed OPFS read or clean-up; a 409 on tus creation (an earlier upload arrived
  after all) is not retried and counts as uploaded.
- 0.1.2 (2026-11-17): AVCHD (.MTS with AC-3 sound) via @mediabunny/ac3 (WebAssembly, loaded only when needed);
  durations of transport streams are end minus first timestamp, like ffprobe; the briefing's target length is saved
  (form.elements['length'] is the field count, not the field).
- 0.1.3 (2026-11-17): header shows logo.svg (with the film strip); target length is free text with suggestions;
  "Other files" (any type, e.g. GPX/KML) in other/, listed in briefing.md; analysis box shows recording span (camera
  time as written, UTC), footage length and space on the server; uploaded clips are recognised by path and size and
  not read again; delete one clip or all (DELETE /api/projects/{pid}/clips[/{cid}], refused while a cut runs);
  "Stop upload"; "Stop analysis" (state `stopped`, continue with the retry button).
- 0.1.4 (2026-11-17): analysis page plays a clip's 360p copy (/projects/{pid}/analysis/clip/{id}.mp4, range
  requests, one at a time); contact sheets and overview pages open in a lightbox (arrows, Escape).
- 0.1.5/0.1.6 (2026-11-17): delete a clip from the analysis page too (round × in the card corner); the page shows only clips Cutlery still has (matched by
  rel_path), so a deleted clip is gone before the next analysis run drops it from clips.json.
- 0.1.7 (2026-11-17): one analysis per project: the runner stops a project's analysis containers left from before
  (label cutlery.kind, or the command for older ones) when it starts a new one. Each web restart (update) used to
  start another `cutter analyze` beside the old ones (four ran at once on the server). Status page facts are read
  live (image/ and transcripts/ files, sidecars for places) and show "n of m analysed" while the analysis runs.
- 0.1.8 (2026-11-17): remaining analysis time (eta.py): per stage (sheets, transcripts, image analysis) the cost in
  wall seconds per second of footage, from the file times of the running analysis, else from WORK/analysis-rates.json
  or earlier runs; shown on the footage and status pages. No GPU for the analysis on the user's server: faster-whisper
  needs NVIDIA/CUDA, the server has an Intel HD 6000 (i5-5250U, 2 cores). `CUTLERY_ANALYSIS_ARGS=--model small` is
  the lever for speed.
- 0.1.9 (2026-11-17): an analysis counts as unfinished while a clip in clips.json has no image/<id>.json (the last
  stage), so a run interrupted by an update is resumed (and its old container stopped) instead of shown as idle.
- 0.1.10 (2026-11-17): analysis page counts a transcript's words from the transcript (clips.json has them only after
  a run), shows transcripts open, and says "transcript to come" for clips not transcribed yet.
- 0.1.11 (2026-11-17): the runner fetches cutter's vision models (YuNet, YOLOX, SFace; 57 MB from GitHub) into
  /models/cutter-vision at start, owned by the job user. The analysis container could not: GitHub is not on the egress
  allowlist (which the agent shares), so faces, people and objects were left out of every image analysis.
- 0.1.12 (2026-11-17): an analysis the user stopped stays stopped across restarts (resume skipped nothing before).
  On the server, the 150 Namibia image analyses made without the vision models were removed to be measured again.
  CLIP tags and PANNs audio events (need PyTorch) stay out for now (user's decision).
- 0.1.13 (2026-11-18): first real Claude run failed: `--allowedTools "Bash(cutter *)"` in dontAsk mode refused every
  other shell command (`cd …; ls`, python, ffmpeg), and the agent looked for the analysis in the data tree. Now the
  agent may use Bash (the job container is the boundary), the prompt names the work folder, and a session that ends
  without cuts/<cut>/edit.json is `failed` (reason no_cut) with Claude's last message.
- 0.2.0 (2026-11-18, cutter 0.5.0): internet access per project: domains the user names in the briefing, notes,
  comments or preferences are reachable for that project's jobs (with subdomains; egress.py, recorded when the
  user saves, never read from files the agent can write); a domain the proxy refuses a running job is asked for on
  the status page (and in Telegram) and allowed for the running job at once (runner maps container address → rules).
  OpenTopoMap tile hosts in the fixed list. Telegram: the operator's bot (token on the settings page or
  CUTLERY_TELEGRAM_BOT_TOKEN), the user links their account by a t.me deep link with a one-time code; messages
  for cut done/paused/failed, analysis done, domain questions (inline buttons). Also: the review page is cutter's own review app
  (served unchanged from the cutter package in a full-width frame, Cutlery's stylesheet added; the host's settings
  person/submit/embedded/ui_language go into the `app` object of /api/cuts) with Cutlery's bar above it ("review
  done → next cut" opens a dialog with notes for the next cut). Notes for a cut (jobs.note, also on "Cut a draft"),
  the project memory CLAUDE.md in the project folder (kept by the agent, editable on the status page) and the
  user's preferences for all projects (DATA/preferences.md, settings page) go into the prompt. cutter.toml gets
  `[filmcraft] export_height = 360`: renders at the stand-ins' size instead of 1080p → 720p. cutter 0.5.0 adds
  general comments (t null), alternatives (cuts/<cut>/alternatives.json, previews alt-<id>-<n>.mp4, entry field
  `alternative`) and the embedding keys.
- One unit-test run had one failure that did not repeat in two further runs (probably a timing test in test_jobs).
- 0.2.1 (2026-11-18, cutter 0.5.1 on branch `review-embed-fit`, b8a1065): people can be merged on the status page
  (tick two or more → "Merge" → dialog with their faces and the name to keep; typing a name another person has
  offers the same; POST /api/projects/<id>/people/merge → `cutter people merge`, then `people name`; arguments
  after "--"). Review page has one scrollbar: cutter 0.5.1 fits its embedded page to a wide frame and posts its
  height on a narrow one (review.js sizes the frame). cutter 0.5.1 also finds an alternatives place by its "clip"
  when shot numbers moved (cut2 of the user's Namibia project had both places on a stale shot 128), shows places
  at the same time together, and has a "⇄ Alternatives: n" jump button.
- 0.2.2 (2026-11-18, cutter 0.5.2): export packages failed with KeyError 'rel_path' when a cut uses external clips
  (map animations, black; edit.json "external_clips"); cutter now puts them into the package under maps/. Merging
  people is a mode: "Merge people" shows the boxes and a bar with "Merge these n" (enabled from two) and "Cancel".
  Claude's messages keep their line breaks (stream.lines) and are shown as Markdown in the job log (static/js/md.js:
  paragraphs, lists, tables, code, bold, links; DOM only, no innerHTML). Older logs were rebuilt from attempt-*.jsonl.
- 0.2.3 (2026-11-18): the project memory is edited in a large dialog (monospace, character count, asks before
  discarding changes) instead of the small field in the narrow column.
- 0.2.4 (2026-11-18): the export check's result (the relink mapping) is kept per browser and restored after a
  reload; before, it lived only in the open page, so a reload or a download started while the check was running
  sent an empty mapping and the package pointed to the footage root instead of the subfolders (seen with
  Sossusvlei: footage in Aaron/, Alex/Videos/, Alex/Gopro/, uploaded with bare file names). Download is disabled
  while the check runs. Hidden files (FilmCraft's .fcproj.lock) are left out of the ZIP.
- 0.2.5 (2026-11-18): cutter 0.5.3. The FilmCraft package refers to its own files (music, maps) relative to the
  project file; FilmCraft resolves them against the project's folder (checked with filmcraft-cli: found after the
  whole folder is moved, missing when only the project is copied elsewhere, kept relative on save). Originals stay
  absolute. Premiere/FCPXML stay absolute: relative URLs are not documented there and could not be tested.
- 0.2.6 (2026-11-18): cutter 0.5.4: the review timeline navigates as in Premiere (wheel scrolls the zoomed
  timeline, Alt/Ctrl + wheel and +/- zoom, \ whole cut / last zoom, arrows by frame, Shift by 5, up/down by edit,
  Home/End, a bar for the visible part while zoomed). Colour correction is planned and started in cutter branch
  `color`: suggestion per shot (exposure, white balance from near-neutral pixels, haze), applied in FilmCraft,
  switchable off in the review (whole film / per shot), before/after stills; decided 2026-11-18: on as a
  suggestion, the review can switch it off. FilmCraft lumetri calibration: exposure in stops (v·2^(e/2.2)),
  temperature ln(R/B)≈0.0034·t, tint ln(G)≈−0.0015·t, blacks +0.0014·b.
- 0.2.7 (2026-11-18): cutter 0.6.0: colour correction (cutter docs/formats.md, "Colour"). Suggested per shot,
  applied in FilmCraft (Color Grading effect), a marker in the other exports; the review shows values, a
  before/after still and switches per shot / whole film; switches carry over to the next cut. Tuned on Sossusvlei
  cut2 (89 of 138 shots corrected, dusk/night/fire left alone). Measuring takes about 2 s per clip once
  (analysis/color.json in the DATA project folder). Not done yet from the plan: looks for the whole film (LUTs,
  presets), the Cutlery briefing option, the analysis view with before/after.
- 0.2.8 (2026-11-19), cutter 0.6.1: material page: /projects/<id>/material, tab "Material". The
  footage by day (sticky day headers, day chips), sorted by recording time; tiles with the middle picture of the film
  strip, hover scrub (a muted video follows the pointer), quality bar (usable/steady/flaws), badges (words, people,
  used in the latest cut, marks); size slider; list view. Filters: search in names and transcripts, camera
  (material.camera(): device, else file-name prefix + short side), person, used/unused in the latest cut, marks,
  speech. Source monitor (dialog): 360p copy, J/K/L, frames, I/O, transcript with words (click jumps, current word
  highlighted, unsure parts in italics), where the clip is used (links to the review), marks: F favourite, X reject,
  for the In/Out range or the whole clip, with a note. Marks go to DATA/projects/<id>/selects.json (format in cutter
  docs/formats.md "selects.json"), into briefing.md ("My selection from the footage") and,
  when there are any, into the job prompt (jobs.SELECTS). Measured on Namibia: 179 clips, 0.26 s, 210 KB.
- 0.2.9 (2026-11-19), cutter 0.7.0: project format (16:9 film, 9:16 reel, 4:5, 3:4, 1:1, 2:3, 4:3, 21:9),
  reframing onto faces and people; see "Formats" below.
- 0.4.0 (2026-11-19): sharing a project with a friend (share.py): Project → Share makes a link
  per person (/s/<id>.<secret>, SHA-256 of the secret in the `shares` table, migration 008; 7/30/90 days or until
  withdrawn); the link sets an HttpOnly cookie, and requests with it and without Basic Auth may only use the paths in
  share.GUEST_PATHS of that project (review page, cutter's review API for cuts/entries – comments under the guest's
  name, own ones changeable; review media; material page and data; suggestions); other HTML pages redirect to the
  review, everything else is 403. A guest's favourites/rejects/tags go into DATA/suggestions.json ("by"), which Claude
  does not read; the owner takes them over (into selects.json, note "(Alex)") or declines them on the material page.
  Telegram/webhook message per guest, collected for two minutes ("Alex wrote 3 comments on cut 2"). Material page:
  "compare with cut N" (any cut): share of the clip used, blue line of the used parts on tiles and in the monitor,
  filter "left out of cut N with ≥ 5 s usable".
- 0.3.1 (2026-11-19): sharp tiles (a 640 px still from the stand-in, made once:
  WORK/posters/<ID>.jpg; the film strip until it is there), the monitor's bar and key hints no longer squeezed in low
  windows; tags managed: own tags renamed / deleted everywhere (popover "Edit"), removed from chosen clips, automatic
  tags hidden as wrong per clip or everywhere (DATA/tags_hidden.json, listed in briefing.md for Claude). Review page:
  the note for the next cut is a field in the bar all through the review, saved as it is typed
  (DATA/next_note.json, PUT /api/projects/<id>/review/note), taken into the "review done" dialog, cleared when the
  job is queued.
- 0.3.0 (2026-11-19), cutter 0.8.0: content tags (CLIP), own tags and tagging several clips on the material page,
  analysis parts per project; see "Tags and analysis parts" below. Namibia tagged on the server (179 clips, 25 min).

## Tags and analysis parts (0.3.0, cutter 0.8.0, 2026-11-19)

Asked for 2026-11-19 ("alle Tags anzeigen, eigene vergeben – ganzer Clip oder Abschnitt", "CLIP automatisch laden",
"Nutzer wählt, was die Analyse auswertet", "für Namibia nachziehen").
- cutter 0.8.0: content_tags.py – CLIP ViT-B/32 as quantized ONNX (Xenova, 155 MB, in image_analysis.MODELS,
  so the runner fetches it with the other models; no torch), a pass of its own after the image analysis (ffmpeg, one
  224 px frame a second), tags per second into analysis/image/<ID>.json with `tags_key` (model + lists), summary,
  quality.txt `content` line; about 10x real time on the server (Namibia: 1925 s of footage in 185 s). Default list
  extended for travel films (gravel road, savanna, river, campfire, camp, aerial …). `[analysis] parts` /
  `--parts` (transcripts, faces, objects, tags, sound, scenes); each clip file records its `parts`, a part added later
  analyses those clips again (tags: only the tag pass). selects.json `kind: "tag"` with `tag`; skill reads them.
- Cutlery: the parts on "new project" and on the setup page (PUT /api/projects/<id>/analysis/parts, a change runs the
  analysis); material page: tiles show own tags (accent) and content tags, tag filter popover (my tags, in the
  picture, things, camera moves; counts, search, AND), monitor: own tags with range (T, I/O), found tags with
  seconds (hover shows them on the bar, click jumps), several clips with Ctrl/Shift-click or the tile box and a bar
  to tag / favourite / reject them at once (POST selects {"items": […]}, same tag not twice); briefing.md
  "My tags"; job prompt mentions them. Labels for content tags, COCO objects and moves in en/de.

## Formats: Reels and other shapes (0.2.9, cutter 0.7.0, 2026-11-19)

Asked for 2026-11-19 ("Reels genauso wie hochkant", "mehr gängige Formate"); decided: reels are their own projects.
- cutter 0.7.0: frame.py (16:9, 9:16, 4:5, 3:4, 1:1, 2:3, 4:3, 21:9, or WxH) per cut (edit.json
  "frame", else [timeline] frame); timeline/FCP7/FCPXML/OTIO sequence size, FilmCraft sequence (tested with the real
  filmcraft-cli: 1080x1920 renders), titles/credits/end cards moved into the frame (credits scaled as a block, 9:16
  keeps out of the top 14 % and bottom 22 %), captions margin, map overview, review render 720 lines on the short
  side, thumbnails, review player in the cut's shape. reframe.py: faces > people > animals once a second on the
  stand-in (YuNet/YOLOX from the analysis models, cached in analysis/reframe.json, ~0.14 s per second of footage on
  the server), a static window per shot; edit.json "focus" overrides. FilmCraft gets clip positions, the review
  render crops there, the exchange formats get a cyan "Reframe shot N" marker (FCP 7 center units are not
  documented well enough). Checked on Namibia clips (faces found in 8 of 8, subject moved into the 9:16 window).
  Skill: Reels guidance in first-cut.md ("Formats").
- Cutlery (branch `frame`): format cards on "New project", a select in the briefing; stored in
  DATA/projects/<id>/cutlery.json, written to cutter.toml [timeline] frame and briefing.md ("Format"); a change pins
  the old frame into the edit lists of earlier cuts. Needs the cutter release (cutter.frame is imported).
- Not done: reframing that follows a moving subject within a shot (keyframes); a reel derived from a film's cut.

## Open questions

- Round trips in Final Cut Pro, DaVinci Resolve and FilmCraft (docs/nle-roundtrip.md), besides Premiere.
- Does Resolve keep volume/opacity keyframes and captions from FCPXML? If not, Resolve should get the FCP 7 XML.

- Face recognition at 360p (M2 measurement).
- HEVC, 4K phone/drone clips, real GoPro GPS tracks and DJI .SRT: none in the available real footage (mostly
  720p and 4K H.264). Needs test material and a run in Safari (Mac) or Chrome on Windows.
- WebKit browser tests need `sudo apt-get install libavif16` on the dev machine.
- Exact Jamendo/Internet Archive download hosts and Natural Earth host for the egress allowlist (M4).
