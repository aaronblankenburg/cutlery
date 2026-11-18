"""Reading Claude Code's stream-json output (`claude -p --output-format stream-json --verbose`).

Every line is one JSON object. `parse_line` turns it into an event for the job log in the UI; `outcome` decides from
the final `result` object (and the exit code) whether the job is done, paused (usage limit, authentication) or failed.
"""
from __future__ import annotations

import datetime
import json
import re

LIMIT = re.compile(r"hit your [\w ]*limit|usage limit|rate[ _-]?limit|limit (has been )?reached|out of (extra )?usage|too many requests|\b429\b|"
                   r"overloaded", re.I)
AUTH = re.compile(r"\b401\b|\b403\b|authenticat|invalid (api|x-api)[ _-]?key|oauth token|token (has )?expired|"
                  r"/login|login expired|not logged in|revoked|credit balance is too low|invalid bearer|billing", re.I)
RESET_EPOCH = re.compile(r"\|(\d{10})\b")
RESET_TEXT = re.compile(r"resets?(?: at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)   # "resets 3:45pm"


def short(text, limit=300):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def lines(text, limit=8000):
    """Claude's own words (Markdown): line breaks kept, trailing spaces and runs of blank lines removed."""
    out, blank = [], 0
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        line = line.rstrip()
        blank = blank + 1 if not line else 0
        if blank < 2:
            out.append(line)
    text = "\n".join(out).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _tool_text(name, inp):
    inp = inp or {}
    if name == "Bash":
        return short(inp.get("command") or inp.get("description"), 200)
    for key in ("file_path", "path", "pattern", "skill", "url", "query"):
        if inp.get(key):
            return short(f"{inp[key]}", 200)
    return ""


def parse_line(line: str) -> list[dict]:
    """Log events of one stream-json line: {"kind": start|tool|think|text|result|error, "text", …}."""
    try:
        msg = json.loads(line)
    except ValueError:
        return [{"kind": "error", "text": short(line)}] if line.strip() else []
    t = msg.get("type")
    if t == "system" and msg.get("subtype") == "init":
        return [{"kind": "start", "text": f"model {msg.get('model', '?')}", "model": msg.get("model"),
                 "session_id": msg.get("session_id")}]
    if t == "assistant":
        out = []
        for part in (msg.get("message") or {}).get("content") or []:
            if part.get("type") == "tool_use":
                out.append({"kind": "tool", "tool": part.get("name"),
                            "text": _tool_text(part.get("name"), part.get("input"))})
            elif part.get("type") == "text" and part.get("text", "").strip():
                out.append({"kind": "text", "text": lines(part["text"], 3000)})
            elif part.get("type") == "thinking" and part.get("thinking", "").strip():
                out.append({"kind": "think", "text": short(part["thinking"], 300)})
        return out
    if t == "result":
        return [{"kind": "result", "text": lines(msg.get("result")), "subtype": msg.get("subtype"),
                 "is_error": bool(msg.get("is_error")), "cost_usd": msg.get("total_cost_usd"),
                 "turns": msg.get("num_turns"), "session_id": msg.get("session_id")}]
    return []


def reset_time(text: str, now: datetime.datetime | None = None) -> str | None:
    """When a usage limit resets, if the message says so (an epoch after '|', or 'resets 6pm')."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    m = RESET_EPOCH.search(text or "")
    if m:
        return datetime.datetime.fromtimestamp(int(m.group(1)), datetime.timezone.utc).isoformat(timespec="minutes")
    m = RESET_TEXT.search(text or "")
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        local = now.astimezone()
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= local:
            at += datetime.timedelta(days=1)
        return at.astimezone(datetime.timezone.utc).isoformat(timespec="minutes")
    return None


def outcome(result: dict | None, exit_code: int | None, tail: str = "") -> dict:
    """{"state": done|paused|failed, "reason", "message", "resume_after"} for a finished run."""
    text = " ".join(x for x in ((result or {}).get("text"), tail) if x)
    if result and not result.get("is_error") and result.get("subtype") in (None, "success") and exit_code in (0, None):
        return {"state": "done", "reason": None, "message": result.get("text"), "resume_after": None}
    if LIMIT.search(text):
        return {"state": "paused", "reason": "usage_limit", "message": short(text, 1000), "resume_after": reset_time(text)}
    if AUTH.search(text):
        return {"state": "paused", "reason": "auth", "message": short(text, 1000), "resume_after": None}
    if result and result.get("subtype") == "error_max_turns":
        return {"state": "failed", "reason": "max_turns", "message": short(text, 1000), "resume_after": None}
    if result is None:
        return {"state": "failed", "reason": "crash", "message": short(tail or f"exit code {exit_code}", 1000),
                "resume_after": None}
    return {"state": "failed", "reason": "error", "message": short(text, 1000), "resume_after": None}
