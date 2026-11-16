<p align="center"><img src="assets/logo.svg" width="96" alt="Cutlery logo: fork and knife beside a film reel with a snipped strip of film"></p>

# Cutlery

Self-hosted web app that cuts films from your own footage – in your browser, without uploading the originals.

Your browser transcodes every clip to a small 360p copy and uploads only that plus the clip's metadata.
The server analyses the footage with [cutter](https://github.com/aaronblankenburg/cutter) and lets Claude Code
cut a draft from your prompt, storyboard, images and music. You review the draft in the browser and comment
on it frame by frame; Claude Code cuts the next version. When you're happy, you download a project for
Premiere Pro, Final Cut Pro, DaVinci Resolve or FilmCraft that links straight to the original files on your
computer, and render the final film there.

The UI is available in English and German.

**Status:** in development (milestones M0–M5 of `docs/plan.md` done, see `docs/progress.md`): upload with browser
transcoding, analysis, Claude Code jobs in isolated containers, review, export for Premiere Pro. Final Cut Pro,
Resolve and FilmCraft exports, hardening and the self-hoster documentation follow.

- `PROMPT.md` – the build brief for Claude Code
- `CLAUDE.md` – standing notes for Claude Code
- `docs/design/` – the agreed GUI designs (Logo, Projects, New project, Status, Review, Export; drawn in German).
  `docs/design/rejected-logos/` keeps the discarded logo ideas for reference.
- `docs/progress.md` – milestone log, kept up to date by Claude Code
- `docs/plan.md` – the approved build plan; `docs/sidecar.md` – clip metadata format;
  `docs/m1-feasibility.md` – transcoding speed and browser findings
- `assets/` – logo (`logo.svg`, `favicon.svg`, `logo-mono.svg`): fork and knife beside a film reel instead of a plate

## Building it

1. Open this folder in Claude Code (version 2.x, logged in).
2. Switch to Opus with `/model`, then press Shift+Tab until plan mode is on.
3. Say:

   > Read PROMPT.md and present the plan.

Claude Code works milestone by milestone and tells you when a milestone needs a different model.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                       # unit tests; browser tests need Google Chrome (and WebKit, see below)
.venv/bin/pytest -m browser            # Playwright: transcoding and upload with generated test clips
```

The browser tests generate their clips with ffmpeg (`tests/fixtures/make_clips.py`). WebKit needs
`.venv/bin/playwright install webkit` and, on Debian/Ubuntu, `sudo apt-get install libavif16`.

## Licence

MIT, like cutter.
