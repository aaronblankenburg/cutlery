<p align="center"><img src="assets/logo.svg" width="96" alt="Cutlery logo: fork and knife beside a film reel with a snipped strip of film"></p>

# Cutlery

Self-hosted web app that cuts films from your own footage – in your browser, without uploading the originals.

Your browser transcodes every clip to a small 360p copy and uploads only that plus the clip's metadata.
The server analyses the footage with [cutter](https://github.com/aaronblankenburg/cutter) and lets Claude Code
cut a draft from your prompt, storyboard, images and music. You review the draft in the browser and comment
on it frame by frame; Claude Code cuts the next version. When you're happy, you download a project for
Premiere Pro, Final Cut Pro, DaVinci Resolve or FilmCraft that links straight to the original files on your
computer, and render the final film there.

The UI is available in English and German. One instance has one user.

## What you need

- A Linux x86_64 machine with Docker and Docker Compose (v2). No GPU; a small home server (Intel NUC class) is enough. The analysis wants a few CPU cores and about 8 GB of memory per job.
- Disk space: the 360p copies are about 1.4 Mbit/s (a few percent of typical camera files), plus a few GB of models and the review renders.
- Claude: either your own Claude subscription (a token from `claude setup-token`, for your personal use only) or an API key from the [Claude Console](https://console.anthropic.com/) (needed if other people use your instance).
- A browser with WebCodecs for the upload: current Chrome or Edge (recommended), Safari; browsers without an HEVC decoder (Firefox) cannot read HEVC footage from phones and drones.
- For access from outside your network: a domain name and a reverse proxy with HTTPS (examples below).

## Install

```sh
git clone https://github.com/aaronblankenburg/cutlery.git && cd cutlery
cp .env.example .env && nano .env          # storage paths, PUID/PGID, port
docker compose run --rm --no-deps cutlery set-password
docker compose up -d
```

Then open <http://127.0.0.1:8080> (or your domain), log in as `cutlery` with the password you set, and enter your
Claude token under **Settings**.

What to set in `.env`:

1. **Storage.** Four directories on the host (see [Storage and backup](#storage-and-backup)). Create them and
   make them yours before the first start; Cutlery refuses to start with a clear message if one is missing or not
   writable, instead of letting Docker create empty directories owned by root:

   ```sh
   mkdir -p data work models secrets && chmod 700 secrets
   ```
2. `PUID` **and** `PGID`: your user and group (`id -u`, `id -g`). Everything Cutlery writes belongs to them.
3. **Claude** – the easiest way needs nothing in `.env`: run `claude setup-token` on any computer with Claude Code
   and paste the token under **Settings** in Cutlery. It goes straight to the runner service, is stored in
   `CUTLERY_SECRETS_DIR` (mode 600, mounted into no other service) and is never shown again; Cutlery warns 30 days
   before it expires (tokens are valid for a year). Until a token is set, uploads and the analysis work and cuts wait.

   Alternatively set exactly one of `CLAUDE_CODE_OAUTH_TOKEN_FILE` or `ANTHROPIC_API_KEY_FILE` in `.env`, each the
   path of a file that contains only the token or key (mode 600). An API key from the Claude Console is required if
   other people use your instance; a setting in `.env` takes precedence over the web UI.
4. Optional: models per job, time zone, notifications (webhook or MQTT) when a cut is ready or a job pauses.
   `.env.example` explains every setting.

### Example: data on an external disk

On a home server with an external disk mounted at `/mnt/hdd`:

```sh
sudo mkdir -p /mnt/hdd/cutlery/{data,work,models,secrets} && sudo chown -R "$(id -u):$(id -g)" /mnt/hdd/cutlery
chmod 700 /mnt/hdd/cutlery/secrets
```

```ini
CUTLERY_DATA_DIR=/mnt/hdd/cutlery/data
CUTLERY_WORK_DIR=/mnt/hdd/cutlery/work
CUTLERY_MODELS_DIR=/mnt/hdd/cutlery/models
CUTLERY_SECRETS_DIR=/mnt/hdd/cutlery/secrets
```

If the disk is not mounted when Docker starts, Cutlery stops with a message naming the missing directory rather
than writing into the empty mount point.

### Reverse proxy and HTTPS

Cutlery listens on `127.0.0.1:8080` only and asks for the password on every page, upload, download and API call.
To reach it from outside, put a reverse proxy with HTTPS in front:

- [`examples/nginx.conf`](examples/nginx.conf) – a server block for a subdomain (upload pieces, long timeouts for uploads and the live job log, the client address for the login lockout).
- [`examples/Caddyfile`](examples/Caddyfile) – the same for Caddy, which also fetches the certificate.
- [`examples/fail2ban/`](examples/fail2ban/) – a filter and a jail that ban addresses with many failed logins. Cutlery itself locks an address out for 15 minutes after 20 failed logins.

**Sharing with friends.** Project → *Share* makes a link per person (`https://…/s/<token>`, valid 7/30/90 days or
until withdrawn). Whoever opens it can watch the cuts, comment (under the name you gave), look through the footage
and suggest favourites, rejects and tags, which you take over or not; nothing else – the password still guards
everything else. Only a hash of the token is stored; withdrawing a link stops it at once. The `/s/` path must reach
Cutlery through the proxy like every other path (the examples do).

If the proxy runs in a Docker container on the same host, set `CUTLERY_BIND=0.0.0.0` only if the port is not
reachable from outside otherwise, and list the proxy's network in `CUTLERY_TRUSTED_PROXIES`.

## How it runs

Two services from one image (`ghcr.io/aaronblankenburg/cutlery`):

- **cutlery** – the web app, uploads, analysis queue and job queue. It has neither the Docker socket nor the Claude credential, runs as `PUID:PGID` without capabilities.
- **runner** – the only service with the Docker socket and the Claude credential (`CUTLERY_SECRETS_DIR` or the file named in `.env`). For each Claude Code job and each analysis it starts a fresh container from the same image that sees only that one project (plus the models, read-only for Claude Code jobs), runs as `PUID:PGID` with a read-only root file system, no capabilities, limited memory and processes, and has no network except an HTTPS proxy that lets through `api.anthropic.com`, a few sources of freely licensed music and map outlines (`CUTLERY_EGRESS_ALLOW`). The container is removed afterwards.

Both restart by themselves (`restart: unless-stopped`) and have health checks; logs go to stdout:

```sh
docker compose ps
docker compose logs -f cutlery runner
```

When Claude's usage limit is reached or the login expires, the job pauses; the project page says why and offers to
continue. Continuing starts a new Claude Code session that picks up from the files of the cut.

## Storage and backup

| Directory             | Contents                                                                                                                                                                                     | Backup                                                                                     |
|-----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| `CUTLERY_DATA_DIR`    | database (projects, jobs, password hash), per project: briefing, storyboard, images, music with licences, clip metadata and manifest, edit lists and decisions of every cut, review comments | **back this up**                                                                           |
| `CUTLERY_WORK_DIR`    | 360p copies, analysis (transcripts, contact sheets, faces), review renders, FilmCraft working files, temporary files of jobs and exports                                                     | can be regenerated; exclude it (for example a separate btrfs subvolume or an exclude rule) |
| `CUTLERY_MODELS_DIR`  | speech and detection models, shared by all projects                                                                                                                                          | downloaded again when missing                                                              |
| `CUTLERY_SECRETS_DIR` | the Claude token entered under Settings                                                                                                                                                      | optional; a new token can be entered any time                                              |

Each tree has the same layout per project, `<dir>/projects/<project-id>/…`. If you lose `CUTLERY_WORK_DIR`, open
the project and pick the footage folder again: the browser makes the 360p copies again, the analysis runs again,
your cuts and comments are still there. Back up `CUTLERY_DATA_DIR` while Cutlery is stopped, or use a file system
snapshot, so the SQLite files are consistent.

Exports are built when you download them and never kept. Deleting a project deletes its files in all trees.
Your original footage never leaves your computer.

## Updates

```sh
docker compose pull && docker compose up -d
```

Database changes are applied on start. To stay on one version, set `CUTLERY_IMAGE=ghcr.io/aaronblankenburg/cutlery:0.1.0`
in `.env`.

## Troubleshooting

| Symptom                                                                               | Cause and fix                                                                                                                                                                                |
|---------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| The container exits: "… does not exist" or "… is not writable"                        | Create the directory and `chown` it to `PUID:PGID`; check that the disk is mounted.                                                                                                          |
| "No password is set" in the browser                                                   | `docker compose run --rm --no-deps cutlery set-password`                                                                                                                                     |
| Cuts wait: "Waiting for Claude access"                                                | Enter a token under Settings, then resume the cut.                                                                                                                                           |
| The runner restarts: "Both a Claude subscription token and an API key are configured" | Keep only one of the two `_FILE` variables in `.env`.                                                                                                                                        |
| "Too many failed logins"                                                              | Wait 15 minutes, or `docker compose restart cutlery`.                                                                                                                                        |
| A job is paused                                                                       | Usage limit or expired login; the project page says which. Create a new token with `claude setup-token`, enter it under Settings, then continue.                                             |
| Uploads stop behind the proxy                                                         | Body size limit (needs ≥ 8 MiB) or timeouts of the reverse proxy; compare with the examples.                                                                                                 |
| Little free space                                                                     | The footer of every page shows the free space per tree and warns below `CUTLERY_DISK_WARN_GB`. Delete finished projects or move `CUTLERY_WORK_DIR` to a larger disk (it can be regenerated). |

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                       # unit tests
.venv/bin/pytest -m browser            # Playwright: transcoding and upload with generated clips (Chrome, WebKit)
```

The browser tests generate their clips with ffmpeg (`tests/fixtures/make_clips.py`). WebKit needs
`.venv/bin/playwright install webkit` and, on Debian/Ubuntu, `sudo apt-get install libavif16`.
Integration tests with Docker and the real cutter image are described in `tests/integration/`.

The image builds `FROM` a pinned cutter image (`Dockerfile`, `CUTTER_IMAGE`) and adds Node and Claude Code at a fixed
version. To try a cutter change: `docker build -t cutter:main ../cutter && docker build --build-arg CUTTER_IMAGE=cutter:main -t cutlery:dev .`, then `CUTLERY_IMAGE=cutlery:dev` in `.env`.

Documentation for contributors:

- `docs/plan.md` – architecture and the split between cutter and Cutlery; `docs/progress.md` – milestone log
- `docs/sidecar.md` – the clip metadata the browser uploads; `docs/standin-analysis.md` – analysis on 360p copies
- `docs/nle-roundtrip.md` – manual checklist for the exports into editing applications
- `docs/design/` – the GUI designs (drawn in German); `assets/` – logo, favicon

Cutlery was built by Claude Code from the brief in `PROMPT.md` (milestones in `docs/plan.md`; `CLAUDE.md` holds the
standing notes).

## Licence

MIT, like cutter. The vendored browser libraries keep their licences (`src/cutlery/static/vendor/VENDOR.md`).