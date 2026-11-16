"""Configuration from the environment.

Every setting comes from an environment variable. In Docker, compose.yaml maps the operator's `.env` to fixed
container paths (`/data`, `/work`, `/models`), so the defaults below are the container paths.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import os
import pathlib
import shlex


class ConfigError(Exception):
    """The configuration is unusable; the text says what to change."""


@dataclasses.dataclass(frozen=True)
class Config:
    data_dir: pathlib.Path
    work_dir: pathlib.Path
    models_dir: pathlib.Path
    host: str = "127.0.0.1"
    port: int = 8080
    trusted_proxies: tuple = ()
    disk_warn_gb: float = 20.0
    upload_expiry_h: int = 72
    cutter_bin: str = "cutter"
    analysis_settle_s: float = 20.0         # wait this long after the last finished upload before analysing
    analysis_parallel: int = 1
    analysis_args: tuple = ()               # extra arguments for `cutter analyze`, e.g. --model small
    # Claude Code jobs
    claude_bin: str = "claude"
    model_draft: str = "claude-opus-5-5"
    model_next: str = "claude-opus-5-5"
    max_turns: int = 400
    claude_args: tuple = ()
    jobs_parallel: int = 1
    job_timeout_s: int = 8 * 3600
    executor: str = "local"                 # local (child process) or runner (isolated container per job)
    skill_dir: str = "/opt/cutter/skills/video-editing"
    token_expires: str = ""                 # date of the subscription token's expiry (YYYY-MM-DD), for a warning
    # notifications
    webhook_url: str = ""
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_topic: str = "cutlery/jobs"
    mqtt_user: str = ""
    mqtt_password: str = ""
    public_url: str = ""

    @property
    def runner_socket(self) -> pathlib.Path:
        return self.work_dir / ".runner" / "runner.sock"

    @property
    def htpasswd(self) -> pathlib.Path:
        return self.data_dir / "htpasswd"

    @property
    def database(self) -> pathlib.Path:
        return self.data_dir / "cutlery.sqlite"


DEFAULT_TRUSTED = "127.0.0.0/8,::1/128,172.16.0.0/12"


def _networks(text: str) -> tuple:
    nets = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError as e:
            raise ConfigError(f"CUTLERY_TRUSTED_PROXIES: '{part}' is not an IP address or network ({e}).")
    return tuple(nets)


def _int(env, name, default):
    raw = env.get(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not '{raw}'.")


def _choice(env, name, options, default):
    value = env.get(name) or default
    if value not in options:
        raise ConfigError(f"{name} must be one of {', '.join(options)}, not '{value}'.")
    return value


def _date(env, name):
    value = (env.get(name) or "").strip()
    if value:
        import datetime
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise ConfigError(f"{name} must be a date like 2027-11-12, not '{value}'.")
    return value


def load(env=None) -> Config:
    env = os.environ if env is None else env
    return Config(
        data_dir=pathlib.Path(env.get("CUTLERY_DATA_DIR") or "/data"),
        work_dir=pathlib.Path(env.get("CUTLERY_WORK_DIR") or "/work"),
        models_dir=pathlib.Path(env.get("CUTLERY_MODELS_DIR") or "/models"),
        host=env.get("CUTLERY_BIND") or "127.0.0.1",
        port=_int(env, "CUTLERY_PORT", 8080),
        trusted_proxies=_networks(env.get("CUTLERY_TRUSTED_PROXIES", DEFAULT_TRUSTED)),
        disk_warn_gb=float(env.get("CUTLERY_DISK_WARN_GB") or 20),
        upload_expiry_h=_int(env, "CUTLERY_UPLOAD_EXPIRY_HOURS", 72),
        cutter_bin=env.get("CUTLERY_CUTTER_BIN") or "cutter",
        analysis_settle_s=float(env.get("CUTLERY_ANALYSIS_SETTLE_SECONDS") or 20),
        analysis_parallel=max(1, _int(env, "CUTLERY_ANALYSIS_PARALLEL", 1)),
        analysis_args=tuple(shlex.split(env.get("CUTLERY_ANALYSIS_ARGS") or "")),
        claude_bin=env.get("CUTLERY_CLAUDE_BIN") or "claude",
        model_draft=env.get("CUTLERY_MODEL_DRAFT") or "claude-opus-5-5",
        model_next=env.get("CUTLERY_MODEL_NEXT") or env.get("CUTLERY_MODEL_DRAFT") or "claude-opus-5-5",
        max_turns=_int(env, "CUTLERY_MAX_TURNS", 400),
        claude_args=tuple(shlex.split(env.get("CUTLERY_CLAUDE_ARGS") or "")),
        jobs_parallel=max(1, _int(env, "CUTLERY_JOBS_PARALLEL", 1)),
        job_timeout_s=_int(env, "CUTLERY_JOB_TIMEOUT_HOURS", 8) * 3600,
        executor=_choice(env, "CUTLERY_EXECUTOR", ("local", "runner"), "local"),
        skill_dir=env.get("CUTLERY_SKILL_DIR") or "/opt/cutter/skills/video-editing",
        token_expires=_date(env, "CUTLERY_CLAUDE_TOKEN_EXPIRES"),
        webhook_url=env.get("CUTLERY_WEBHOOK_URL") or "",
        mqtt_host=env.get("CUTLERY_MQTT_HOST") or "",
        mqtt_port=_int(env, "CUTLERY_MQTT_PORT", 1883),
        mqtt_topic=env.get("CUTLERY_MQTT_TOPIC") or "cutlery/jobs",
        mqtt_user=env.get("CUTLERY_MQTT_USER") or "",
        mqtt_password=env.get("CUTLERY_MQTT_PASSWORD") or "",
        public_url=env.get("CUTLERY_PUBLIC_URL") or "",
    )


# ---------------------------------------------------------------- Claude authentication

@dataclasses.dataclass(frozen=True)
class ClaudeAuth:
    kind: str           # "oauth" (subscription token, operator's own use) or "api_key"
    variable: str       # the environment variable claude reads
    secret: str

    def env(self) -> dict:
        return {self.variable: self.secret}


AUTH_VARIABLES = {"oauth": "CLAUDE_CODE_OAUTH_TOKEN", "api_key": "ANTHROPIC_API_KEY"}

AUTH_HELP = ("Set exactly one of them in .env: CLAUDE_CODE_OAUTH_TOKEN_FILE (a token from 'claude setup-token', "
             "only for your own personal use of your Claude subscription) or ANTHROPIC_API_KEY_FILE (a key from the "
             "Claude Console, required if you offer Cutlery to other people). The variables without _FILE work too.")


def _secret(env, name):
    """The value of NAME, or the content of the file named by NAME_FILE (Docker secrets). Empty means unset."""
    direct = (env.get(name) or "").strip()
    file_name = (env.get(name + "_FILE") or "").strip()
    if direct and file_name:
        raise ConfigError(f"Both {name} and {name}_FILE are set; keep only one.")
    if not file_name:
        return direct
    path = pathlib.Path(file_name)
    try:
        st = path.stat()
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise ConfigError(f"{name}_FILE points to {path}, which does not exist.")
    except OSError as e:
        raise ConfigError(f"{name}_FILE: cannot read {path} ({e.strerror}).")
    if st.st_mode & 0o077 and not str(path).startswith("/run/secrets/"):
        raise ConfigError(f"{path} can be read by other users; run: chmod 600 {path}")
    if not value:
        raise ConfigError(f"{path} is empty.")
    return value


def claude_auth(env=None) -> ClaudeAuth:
    """Exactly one way to authenticate claude must be configured, otherwise this raises ConfigError."""
    env = os.environ if env is None else env
    found = {kind: _secret(env, var) for kind, var in AUTH_VARIABLES.items()}
    found = {k: v for k, v in found.items() if v}
    if not found:
        raise ConfigError("No Claude authentication is configured. " + AUTH_HELP)
    if len(found) > 1:
        raise ConfigError("Both a Claude subscription token and an API key are configured. " + AUTH_HELP)
    kind, secret = next(iter(found.items()))
    return ClaudeAuth(kind, AUTH_VARIABLES[kind], secret)
