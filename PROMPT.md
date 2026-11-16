# Build brief for Claude Code: Cutlery

Build **Cutlery** in this repository. Cutlery is an open-source, self-hosted web app. It lets one
person cut films from their own video footage in the browser, without uploading the large original
files. The German UI uses the name **SchnippSchnapp** (see "Languages and naming").

Cutlery builds on cutter (https://github.com/aaronblankenburg/cutter, MIT). cutter provides footage
analysis, an edit plan written by an AI agent using the video-editing skill, music with beat sync,
timelines (OTIO, FCP7 XML, SRT), FilmCraft finishing and a review app.

Before anything else, read these parts of cutter:

- AGENTS.md and README.md
- skills/video-editing/
- src/cutter/, especially analyze.py, image_analysis.py, telemetry.py, proxies.py, timeline.py,
  filmcraft.py, feedback.py and review_app/
- Dockerfile and compose.yaml

Also read the GUI designs in docs/design/.

Everything in the repository is written in English: code, comments, docs, commit messages. UI text
lives in translation files (see "Languages and naming").

## Audience

Cutlery is published under the MIT licence so that others can self-host it.

- Each instance has exactly one user, its operator. There are no multi-user accounts.
- Nothing in the code or docs may be specific to my setup. Hostnames, paths, my reverse proxy and my
  notification setup are configuration or documentation examples, never defaults that only work for me.
- The README explains, for someone who has never seen cutter, what Cutlery does, what it needs
  (Docker host, Claude account, a supported browser) and how to get it running.

## Principle: depend on cutter, don't copy it

- Cutlery uses cutter as a dependency. Its image builds on the cutter image
  (ghcr.io/aaronblankenburg/cutter, version and digest pinned). Do not copy cutter code into this
  repository or patch it here.
- Changes that are useful without the web app belong in cutter. Make them there on a separate
  branch, with separate commits, tests and docs. cutter's local CLI and plugin workflow must keep
  working unchanged, and all its existing tests must stay green.
- At least these four changes are expected there:
  - **a)** Original metadata comes from a sidecar file per clip, while the analysis reads a smaller
    stand-in file (see "Server: analysis").
  - **b)** The skill gets a non-interactive mode.
  - **c)** Export supports mapping paths to another machine.
  - **d)** A FCPXML writer.
- Before starting, list what belongs in cutter and what belongs in Cutlery, and wait for my approval.

## Target workflow

1. **Create a project.** The user picks their footage folder in the browser. Footage is up to 4K and
   often HEVC from phones, DJI drones or GoPros. The browser transcodes every clip locally to 640 px
   wide (360p) and uploads only these small files. The user also enters:
   - a free-text prompt,
   - briefing fields: purpose and audience, target length, style, music wishes, no-gos and people who
     must not appear,
   - optionally a storyboard (text, PDF or images),
   - additional images,
   - their own music, with a mandatory licence statement or "my own music".
2. **"Cut a draft" button.** On the server, Claude Code runs headless with the video-editing skill
   and cuts the first draft.
3. **Review.** The user watches the cut and leaves time-coded comments. The button "Review done →
   next cut" locks the comments of that cut and starts the skill's next-cut flow: apply the feedback
   and answer every comment. This can repeat any number of times.
4. **"Done → Export" button.** The user downloads a ZIP package for Premiere Pro, Final Cut Pro,
   DaVinci Resolve or FilmCraft. Its media references point to the ORIGINAL FILES on the user's own
   computer. The final render happens in their editing application.

## Languages and naming

- The UI is fully translatable. English and German ship from the start; adding a language means
  adding one translation file, with no code changes.
- The default language follows the browser (`Accept-Language`), falling back to English. A switch in
  the header changes it; the choice is remembered (cookie or localStorage).
- The product name is part of the translation: "Cutlery" in English, "SchnippSchnapp" in German
  (written exactly like that, with the wordmark "Schnipp" in the text colour and "Schnapp" in the
  accent colour). Page titles, the wordmark in the header and the export README follow the language.
- The repository, image, package and CLI names are always `cutlery`.
- Dates, numbers, file sizes and durations are formatted per locale.
- The briefing the user writes stays in whatever language they write it in; the agent answers review
  comments in the language of the comment.

## GUI designs

- docs/design/ holds the agreed GUI designs:

  | File | Page |
  |---|---|
  | Main | Projects |
  | Neu | New project |
  | Job | Status |
  | Review | Review |
  | Export | Export |
  | Logo, LogoVarianten | Logo and wordmarks (variant C, film strip, is the chosen logo) |

  They are the binding template for layout, wording, colours, fonts (Space Grotesk, Inter,
  JetBrains Mono), spacing and states.
- The designs are drawn in German. Their German texts are the starting point for the German
  translation; write the English texts so they say the same thing in natural English.
- The files are template HTML. `{{…}}` are placeholders, `<sc-for>` and `<sc-if>` are loops and
  conditions, and `renderVals()` supplies sample data. Take structure and style from them, but
  neither the template syntax nor the sample data.
- First derive design tokens (colours, radii, spacing, typography) as CSS variables and build the
  pages on top of them.
- Where the designs leave things open (analysis view with contact sheets and transcripts, error
  states, mobile layout), decide in the same style and show me.
- The review page replaces the frontend of cutter's review app. The review app's API and storage stay
  as they are.
- Keep the frontend lean. Server-rendered HTML with a little JavaScript, or a small framework without
  a heavy build, is enough. Propose what fits cutter's review app best.

## Browser: transcoding, metadata, upload

**Transcoding**

- Use WebCodecs with Mediabunny, or mp4box.js plus a muxer.
- Read the original file in chunks with `File.slice`; never load it into memory as a whole.
- Decode, scale to 640 px wide (respecting rotation) and encode as H.264 at about 1–1.5 Mbit/s.
- Audio as AAC at 48 kHz, about 160 kbit/s, with the original channel count.
- Keep the proxy contract from cutter's proxies.py: same frames at the same times, no frame-rate
  change, timestamps preserved even with variable frame rate (VFR).
- Before starting, check `VideoDecoder.isConfigSupported()` for each clip. Report codecs and
  containers that cannot be decoded (MTS, MXF, AVI …) clearly, with a browser recommendation. Never
  skip anything silently.

**Metadata**

- Write a sidecar JSON per clip from the ORIGINAL with:
  - file name and rel_path (`webkitRelativePath`)
  - size and mtime
  - duration, r_frame_rate and avg_frame_rate
  - width, height and rotation
  - codec and bit depth
  - creation time and start timecode
  - audio channels and sample rate
  - all container tags (device, ISO 6709 GPS)
  - a fingerprint: size plus SHA-256 of the first and the last 1 MiB
- Telemetry: extract the GoPro gpmd track raw and upload it too. Upload DJI .SRT files that sit next
  to the clips as well.

**Upload**

- The upload is resumable (e.g. tus) and runs clip by clip while transcoding is still going on.
- Progress and resumption survive a tab reload; keep the state in OPFS or IndexedDB.
- Hold a wake lock while working.
- Target current Chromium browsers and Safari. Firefox only as far as it works without extra effort.

## Server: analysis

- The 360p files take the role of cutter's proxies (`proxies.table` / `timeline.use_proxies`). No
  separate proxy generation.
- In clips.json all original metadata comes from the sidecar. Only the media path used for analysis
  points to the 360p file (change a in cutter).
- `time_from_name()` sees the original name. telemetry.py reads the uploaded gpmd track or SRT file.
- Analysis starts incrementally as soon as clips arrive, not when the button is pressed.
- Check whether the thresholds in quality.py and image_analysis.py behave noticeably differently on
  360p H.264 than on the originals, especially the Laplacian sharpness. Document the result. Adjust
  thresholds only if needed, and then through cutter.toml.
- Review renders are made from the 360p files at 720p.

## Server: Claude Code as a job

**Non-interactive skill mode** (change b in cutter)

- If a briefing.md exists in the project, the skill asks no questions. It decides missing points
  itself and writes every assumption into the decisions file, so it shows up in the review.
- It does not ask before downloads either. Models (Whisper, detection models) live in a persistent
  volume and are fetched on first start.

**Queue and invocation**

- The job queue lives in SQLite. One job per project; parallelism is configurable (default 1).
- Jobs run `claude -p` with `--output-format stream-json` in the project folder. Stream progress live
  to the UI.
- Every cut is a fresh session. The project files provide the context.
- The model is configurable (.env, passed to claude with `--model`), separately for the first draft
  and for follow-up cuts. Default: Opus 5.5 for both; Sonnet 5.5 must be easy to set for follow-up
  cuts with little feedback. The model used is shown in the job log and in the UI.

**Authentication**

- Two ways, configured in .env; exactly one must be set, otherwise refuse to start with a clear
  message:
  - `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`, for operators running Cutlery for their own
    personal use on their Claude subscription. This is my setup.
  - `ANTHROPIC_API_KEY` from the Claude Console, for everyone else.
- The docs state plainly that a subscription token is for the operator's own personal use, and that
  anyone offering Cutlery to other people must use an API key.
- Secrets are passed as files with mode 600 or as Docker secrets, never baked into the image.
- No `--bare` when using the subscription token (bare mode does not read OAuth credentials).
- The first start must not hang on onboarding (`~/.claude.json`). Read the current Claude Code docs on
  headless use / `-p` and follow their flags rather than guessing.

**Pauses and expiry**

- Detect usage-limit and auth errors in the stream-json result. The job is then set to "paused", not
  "failed", and the UI shows it.
- Resuming is a button. The resumed job starts a fresh session from the state of the project files.
- With a subscription token, the UI warns when it expires in less than 30 days. The expiry date is a
  config value.

**Containing the agent**

- It runs in its own container that sees only the current project and the models.
- It has a tool allowlist: cutter commands, reading and writing inside the project.
- Turn limit and timeout are set.
- Network egress only as far as needed. Clarify with me which hosts the music search needs.

**Notifications (optional)**

- On job end or pause, send a message via a webhook URL and/or MQTT (host and topic configurable,
  MQTT without auth must work too).

## Export with correct media references (core requirement)

**Flow on the export page**

- The user picks their operating system and enters the absolute path of their footage folder. The UI
  explains how to copy it: "Copy as path" on Windows, ⌥⌘C on macOS.
- Then the user picks the folder in the browser again, without uploading. The browser checks every
  clip against the manifest by fingerprint. It detects renamed or moved files and reports missing
  ones before exporting.
- Export files are generated at download time.
- The UI recommends unzipping the package into a `_cutlery/` subfolder of the footage folder. Then a
  single base path also covers music, title PNGs, maps and overview graphics in the package.

**Timeline and paths**

- `timeline.build()` runs WITHOUT proxies, with the original width, height, fps, duration, start
  timecode and rotation from the sidecar.
- Only then do the scaling (Basic Motion 50 % for 4K) and the editors' relink criteria match.
  Premiere's Link Media compares the media start among other things; Final Cut Pro checks media type
  and duration.
- Path generation is its own, thoroughly tested function (change c in cutter). It handles
  `file://localhost/` with URL encoding, Windows drive letters and Unicode normalisation (NFC vs. NFD
  on macOS).

**Formats**

For each format the UI says honestly what comes along and what the user has to redo.

- **a) Premiere:** FCP7 XML and SRT. Titles as transparent PNGs on their own video track, music
  ducking as volume keyframes. Markers flag stabilisation, audio cleanup and the review decisions.
- **b) Final Cut Pro:** a dedicated FCPXML writer (change d), since the OTIO adapter is not enough. It
  writes cuts, transform, titles, captions, volume keyframes and markers. Stabilisation and noise
  reduction only if the current FCPXML DTD supports them; check against the DTD.
- **c) DaVinci Resolve:** FCPXML and/or OTIO, whichever preserves more.
- **d) FilmCraft:** the project produced by cutter's `finish`, with media references switched from
  the 360p files to the originals. Find out from filmcraft-cli and the FilmCraft docs whether relink
  or proxy switching exists. If not, propose an approach and ask me before rewriting the project
  format directly.

**Package contents and privacy**

- The package contains the music licence information and a short README per editing application, in
  the UI language.
- Face embeddings (people.json) never go into review data or exports.
- Deleting a project deletes all its data.

## Access

- One user per instance.
- The whole app is protected by HTTP Basic Auth: UI, API, uploads, media, review and export. No
  exceptions, not even health checks from outside.
- Use the mechanism of cutter's review app (password hash, lockout after failed attempts per IP) or
  an equivalent small implementation. The password is set with a single command.
- The app is meant to run behind a reverse proxy with HTTPS and listens on 127.0.0.1 by default.
- Ship as examples: an nginx server block for a subdomain (proxy to the container, large uploads or
  tus chunks, long timeouts for uploads and job streams), the same for Caddy, and a fail2ban filter
  for failed logins.

## Deployment (as little setup as possible)

**Target machine:** any Linux x86_64 host with Docker and Compose. No GPU required. My own is an
Intel NUC home server; keep it working on hardware of that class.

**Target flow:**

1. Clone the repo.
2. Copy `.env.example` to `.env` and adjust: data path, port, auth (token file or API key), optional
   notifications.
3. Set the password with a one-line command.
4. `docker compose up -d`.

Nothing more.

**Image and services**

- One image, built by CI (GitHub Actions → ghcr.io/aaronblankenburg/cutlery), FROM the pinned cutter
  image, plus Node and Claude Code at a fixed version.
- Compose with as few services as possible: web and queue in one service, agent jobs in a separate
  container from the same image.
- If that doesn't work cleanly without the Docker socket in the web container, propose the simplest
  safe alternative and explain it.

**Data and operation**

- All data lives under one host folder (projects, SQLite, uploads), plus a volume for models.
- `restart: unless-stopped`, health checks, logs to stdout.

**Updates and backup**

- Updates are `docker compose pull && docker compose up -d`. Database migrations run automatically.
- Document which folders belong in a backup and which don't. 360p uploads and renders can be
  regenerated; comments and edit plans cannot.

## Tests

- **Unit tests** for:
  - path encoding,
  - sidecar metadata,
  - matching by manifest and fingerprint,
  - job states, including "paused" on usage limit, with a simulated claude,
  - the auth configuration check (exactly one of token or API key),
  - translations (every key present in every language),
  - all export writers.
- **Golden tests** modelled on cutter/tests/golden/demo for FCP7 XML, FCPXML and OTIO with original
  metadata.
- **Browser tests** (Playwright) for transcoding and upload, with small test clips:
  - H.264 and HEVC,
  - portrait with rotation,
  - VFR,
  - a clip with a start timecode.
- **Checklist docs/nle-roundtrip.md** for manual testing:
  - Material: 4K HEVC drone clips, portrait phone clips, GoPro.
  - Flow: export, then import into Premiere, Final Cut Pro, Resolve and FilmCraft.
  - Expected: cuts frame-accurate as in the draft, all media linked.

## Working method

First present a plan with milestones and the split between cutter and Cutlery, and wait for my
approval. Proposed milestones:

| Milestone | Content | Model |
|---|---|---|
| M0 | Plan, cutter / Cutlery split, architecture | Opus 5.5 (plan mode) |
| M1 | Browser transcoding, sidecar and upload; feasibility on my real footage | Opus 5.5 |
| M2 | Analysis on 360p with sidecar metadata | Sonnet 5.5 |
| M3 | Export with correct paths (Premiere first) and round-trip test | Opus 5.5 |
| M4 | Claude Code jobs and non-interactive skill mode | Opus 5.5 |
| M5 | Review buttons, GUI after docs/design/, English and German UI | Sonnet 5.5 |
| M6 | FCPXML, Resolve and FilmCraft export | Opus 5.5 |
| M7 | Deployment, hardening, README and docs for other self-hosters | Sonnet 5.5 |

**Model choice.** The "Model" column is binding. You cannot switch models yourself; I do that with
/model. Therefore:

- At the start of every milestone, name the intended model. If you are running on a different one,
  tell me and wait until I have switched.
- Split milestones so that model switches happen at clean points: tests green, state committed, open
  points noted in docs/progress.md. A new session must be able to continue from there without prior
  knowledge.
- If a Sonnet milestone turns out to be harder than expected (e.g. time base, codecs, relink
  criteria), suggest switching to Opus for that part.
- Fable models only when I explicitly say so.

After every milestone, run the tests, report briefly and collect open questions. When something is
unclear, ask instead of guessing, especially about:

- the FilmCraft project format,
- the egress allowlist,
- anything that changes cutter's existing workflow.

I write to you in German; answer me in German. Everything you write into the repository stays in
English.
