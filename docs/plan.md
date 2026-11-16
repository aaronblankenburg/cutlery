# Cutlery – build plan (M0, approved)

Cutlery is a self-hosted web app (MIT, one user per instance) that brings cutter's pipeline – analysis, an edit
plan written by Claude Code with the video-editing skill, timelines, FilmCraft, review – into the browser. The
original footage stays on the user's computer: the browser transcodes it to 360p and uploads only those files
plus sidecar metadata; the export points back to the originals on the user's computer.

Basis: PROMPT.md, cutter at 9ddae25 (0.2.1), docs/design/.

## Findings in cutter that shape the plan

- `analyze.probe_clip` is the only place clips.json metadata is built; clip IDs are keyed by `path`
  (`assign_ids`). Bit depth, container tags and a fingerprint are not recorded.
- Proxies (`analysis/proxies/proxies.json`) stay valid via `stat(clip.path)`, which would check the stand-in, not
  the original. `image_analysis.run(proxies=…)` already separates picture path and clip path.
- `telemetry.read` extracts gpmd from the clip itself (absent in a re-encoded stand-in) or reads `X.SRT` next to
  the clip. `gopro()` and `dji_srt()` are pure parsers.
- quality.py thresholds are module constants. Sharpness is relative within a clip and measured at 480 px anyway.
  At risk at 360p: face recognition (`FACE_PX = 48`) and `flag_low_detail` (groups clips by width×height).
- timeline.py: media URLs are made in one place (`ext_ref`, `Path.as_uri()`, POSIX only). Start timecode is always
  `00:00:00:00`, `ntsc` is always FALSE, the sequence is fixed 1920×1080. Music ducking, titles, stabilisation and
  audio cleanup exist only in the FilmCraft batch, not in OTIO/FCP7. No FCPXML.
- filmcraft.py builds `.fcproj` only through `filmcraft-cli` (`file.import` of the FCP7 XML). No relink command
  is known.
- Review app: standard-library server; `/api/cuts`, `/api/entries`, `/api/status`, `/api/export`,
  `/media/<cut>/…` with ranges; SQLite `feedback.sqlite`; Basic Auth with scrypt htpasswd and per-IP lockout.
  Comments cannot be locked per cut; `person` is free text.
- The skill asks interactively for: briefing, downloads, naming people, unclear music licences, review app and
  passwords, deploy, deleting, conflicting reviewers, manual FilmCraft edits.
- `[paths]` in cutter.toml is configurable per entry.

## Split between cutter and Cutlery

### cutter (branch `cutlery-support`; separate commits, tests and docs; CLI and plugin unchanged)

| # | Change | Core |
|---|---|---|
| a | Sidecar metadata, analysis on a stand-in | `X.mp4` + `X.cutter.json` (schema in docs/formats.md). `probe_clip` takes all original values from the sidecar; `path` stays the stand-in; new field `original {name, rel_path, size, mtime, fingerprint, …}`. `time_from_name` on the original name. IDs stable via fingerprint. Stand-ins count as their own proxies. `telemetry.read` reads a raw `X.gpmd` first. `flag_low_detail` groups by analysed size. Thresholds configurable via cutter.toml only if the 360p measurement requires it. |
| b | Non-interactive skill mode | With `briefing.md` in the project: no questions. The agent decides missing points and records every assumption in `decisions.json` (new kind `assumption`, visible in review). No download prompts. Uses existing people names. Unclear music licence: track not used, assumption recorded. No `serve`/`deploy`/passwords. Replies in the language of each comment. |
| c | Path mapping to another machine | New `cutter/paths.py`: `file://localhost/` with percent-encoding, Windows drive letters, UNC, NFC/NFD. `timeline.build(..., target=…)` uses it for clips, music, maps and titles. CLI: `cutter timeline cutN --target-os windows --target-root 'D:\Videos\Trip'`. Package files live under `<root>/_cutlery/`. |
| d | FCPXML writer | New `cutter/fcpxml.py`, checked against the DTD: cuts, transform, titles, captions, volume keyframes, markers; stabilisation and noise reduction only if the DTD supports them. Golden test. |
| e | Timeline fidelity for NLE relink | Real start timecode, correct NTSC rates, rotation, music ducking as volume keyframes in FCP7, markers for stabilisation, audio cleanup and review decisions, titles as transparent PNGs on their own video track. |
| f | Small items | `paths.review_media` separate from the review SQLite (renders in WORK, comments in DATA); review renders at 720p from 360p sources. |

### Cutlery

- Web app (UI, API, i18n, auth), job queue, agent container control, egress control, notifications.
- Browser: transcoding, sidecar, telemetry extraction, tus upload, manifest matching at export.
- Project creation (`cutter.toml`, `briefing.md`, storyboard, images, music with licences) in cutter's layout.
- Review frontend in Cutlery's design on cutter's review API and storage; "review done" lock enforced by Cutlery in
  front of write requests (cutter's storage unchanged).
- Export package (ZIP built at download time), README per editing application in the UI language, licence info,
  never people.json.
- Deployment: image `FROM ghcr.io/aaronblankenburg/cutter@<digest>`, compose.yaml, nginx/Caddy/fail2ban examples.

## Architecture

- **Server:** Python 3.12, Starlette + uvicorn, Jinja2 server-rendered pages, SQLite with numbered SQL
  migrations. cutter's review logic is imported as a library, not copied; the review API is mounted per project
  with the same protocol.
- **Frontend:** vanilla JS ES modules, no build step, strict CSP. Vendored and pinned with hashes: Mediabunny
  (MPL-2.0, unmodified file), tus-js-client (MIT). Fonts self-hosted as woff2 (OFL). Design tokens as CSS
  variables derived from docs/design.
- **i18n:** `cutlery/locales/<lang>.json`, shared by server and client. Language: cookie → `Accept-Language` →
  English. Locale formatting with Babel (server) and `Intl` (client).
- **Services:** `cutlery` (web, queue, tus, analysis triggers; no Docker socket, no Claude secrets) and `runner`
  (same image; the only holder of the Docker socket; accepts only "run job X for project P" on a Unix socket and
  starts a fixed container spec: only that project's data and work dirs plus models read-only, PUID/PGID,
  `cap-drop ALL`, `no-new-privileges`, read-only rootfs, temp files in WORK, memory/PID limits, internal network
  behind an allowlisting egress proxy). Analysis runs as a job type without Claude and without egress.
- **Claude jobs:** `claude -p … --output-format stream-json --verbose --model <M> --max-turns N --allowedTools …`,
  a fresh session per cut. Exactly one of `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` (as files), otherwise
  refuse to start. No `--bare` with OAuth. Flags and onboarding are verified against the current headless docs
  in M4. Usage-limit and auth errors → `paused`; resume starts a fresh session. Models via `.env`
  (`CUTLERY_MODEL_DRAFT`, `CUTLERY_MODEL_NEXT`, default `claude-opus-5-5`). Token expiry as a config value, warning
  under 30 days. Optional webhook and MQTT notifications.
- **Egress allowlist (defaults, configurable):** api.anthropic.com; music: freemusicarchive.org, ccmixter.org,
  commons.wikimedia.org, upload.wikimedia.org, archive.org and its download hosts, jamendo.com and its API/CDN
  hosts (exact hosts determined in M4); the Natural Earth source used by `cutter map`. Claude Code non-essential
  traffic disabled. Models are fetched once by the analysis job, not by the agent.
- **Storage** (same container paths `/data`, `/work`, `/models` everywhere):

  ```
  DATA/cutlery.sqlite   projects, jobs, settings, upload state
  DATA/projects/<id>/   cutter.toml, briefing.md, storyboard/, images/, music/ (+ licences),
                        sidecars/, manifest.json, cuts/cutN/{edit,decisions,plan}.*, .cutter/review/feedback.sqlite
  WORK/projects/<id>/   media/ (360p, .gpmd, .SRT), analysis/, cut work files, FilmCraft projects, timelines,
                        review-media/, exports/, tmp/
  ```

  cutter's `[paths]` points the regenerable parts to WORK. On start, every directory must exist and be writable
  as PUID, otherwise fail with a clear message.
- **Auth:** Basic Auth on everything, scrypt hash and per-IP lockout from cutter's implementation, password set
  with one command, bind 127.0.0.1 by default.
- **Export:** the browser matches the chosen folder against `manifest.json` by fingerprint and sends only the
  mapping clip id → rel_path. The server builds the timeline without proxies, with original metadata and the
  target OS and path, and streams the ZIP. FilmCraft (M6): rebuild the project from the original timeline with
  target paths; whether filmcraft-cli can relink is checked in M6; rewriting the project format only after asking.

## Milestones

| M | Content | Model | Tests |
|---|---|---|---|
| M0 | This plan | Opus 5.5 | – |
| M1 | Scaffold, compose.yaml (storage layout, start check), Basic Auth, minimal project creation, browser transcoding, sidecar, gpmd/SRT, resumable tus upload, wake lock, codec check; feasibility on real footage | Opus 5.5 | Unit: sidecar, fingerprint, auth config; Playwright: H.264, HEVC, rotation, VFR, start TC |
| M2 | cutter a (+f): analysis on 360p with sidecar, incremental; threshold comparison documented | Sonnet 5.5 | cutter tests green; integration test with demo footage |
| M3 | cutter c + e (Premiere first); export page with manifest matching, ZIP | Opus 5.5 | Path units, matching; golden FCP7/OTIO with original metadata; docs/nle-roundtrip.md |
| M4 | cutter b; queue, runner, agent container, egress, stream-json, pause/resume, notifications | Opus 5.5 | Job states with a simulated claude; auth check |
| M5 | GUI after the designs, analysis view, error and mobile states, review frontend and buttons, English and German | Sonnet 5.5 | Translation completeness; Playwright page smoke tests |
| M6 | cutter d (FCPXML), Resolve, FilmCraft export | Opus 5.5 | Golden FCPXML with DTD validation; writer unit tests |
| M7 | CI to ghcr.io, hardening, README and self-hoster docs, nginx/Caddy/fail2ban, backup rule | Sonnet 5.5 | Full suite; deployment check |

## Open points

- FilmCraft relink and FCPXML stabilisation support (M6).
- Whether face recognition stays usable at 360p (M2 measurement).
- The export design shows a `_cutter` subfolder; the brief says `_cutlery/`. Cutlery uses `_cutlery/`.
