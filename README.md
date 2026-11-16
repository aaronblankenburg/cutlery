# SchnippSchnapp

Self-hosted web app for cutting films from your own footage in the browser – without uploading the originals.
The browser transcodes every clip to 360p and uploads only those small copies plus their metadata; the server
runs [cutter](https://github.com/aaronblankenburg/cutter) and Claude Code to cut drafts, you review them in the
browser, and at the end you download a project for Premiere Pro, Final Cut Pro, DaVinci Resolve or FilmCraft that
links to the original files on your computer.

**Status:** planning. Nothing is built yet.

- `PROMPT.md` – the build brief for Claude Code (German)
- `docs/design/` – the agreed GUI designs (Logo, Projects, New project, Status, Review, Export)

## Getting started

Open this folder in Claude Code and say:

> Lies PROMPT.md und leg mir den Plan vor.
