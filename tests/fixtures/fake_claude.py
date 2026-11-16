#!/usr/bin/env python3
"""A simulated `claude -p … --output-format stream-json` for tests and development without a Claude account.

It prints the stream-json messages a real run prints (system init, assistant text and tool calls, tool results,
result) and writes what a finished cut leaves in the project: cuts/<cut>/edit.json and decisions.json (with an
assumption). The scenario comes from FAKE_CLAUDE_SCENARIO:

    ok           a finished cut (default)
    usage_limit  stops with "You've hit your session limit · resets 3:45pm"
    auth         stops with an expired OAuth token
    max_turns    result subtype error_max_turns
    crash        exits without a result
    slow         like ok, but takes FAKE_CLAUDE_SECONDS (default 30) seconds

FAKE_CLAUDE_LOG=<file> appends the arguments and the relevant environment of every call (to check the command).
"""
import json
import os
import pathlib
import re
import sys
import time

argv = sys.argv[1:]
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                            "auth": sorted(k for k in os.environ if k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")),
                            "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
                            "nonessential": os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")}) + "\n")
scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "ok")
prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
model = argv[argv.index("--model") + 1] if "--model" in argv else "claude-opus-5-5"
m = re.search(r"\b(cut\d+)\b", prompt)
cut = m.group(1) if m else "cut1"
session = "00000000-0000-4000-8000-%012d" % (abs(hash(prompt)) % 10 ** 12)


def emit(obj):
    print(json.dumps(obj), flush=True)


emit({"type": "system", "subtype": "init", "model": model, "session_id": session, "cwd": os.getcwd(),
      "tools": ["Bash", "Read", "Write", "Skill"]})
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": f"Reading briefing.md for {cut}."}]}})
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                                    "input": {"file_path": "briefing.md"}}]}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "…"}]}})
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                                    "input": {"command": "cutter analyze --json"}}]}})

if scenario == "slow":
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SECONDS", "30")))
if scenario == "crash":
    print("node: segmentation fault", file=sys.stderr)
    sys.exit(139)
if scenario == "usage_limit":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
          "result": "You've hit your session limit · resets 3:45pm", "num_turns": 3, "total_cost_usd": 0.0,
          "session_id": session})
    sys.exit(1)
if scenario == "auth":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
          "result": "OAuth token has expired · Please run /login", "num_turns": 0, "session_id": session})
    sys.exit(1)
if scenario == "max_turns":
    emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 400, "session_id": session})
    sys.exit(1)

d = pathlib.Path("cuts") / cut
d.mkdir(parents=True, exist_ok=True)
(d / "edit.json").write_text(json.dumps({"title": f"Film ({cut})", "fps": 25, "sections": [], "music": []}))
(d / "decisions.json").write_text(json.dumps({"format": 2, "version": cut, "entries": [
    {"id": "length", "kind": "assumption", "title": "Length", "text": "About 5 minutes.",
     "why": "The briefing gave no length."}]}))
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": f"{cut} is ready: 5:02, 4 sections."}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": f"{cut} is ready: 5:02, 4 sections.",
      "num_turns": 42, "total_cost_usd": 3.21, "duration_ms": 1234, "session_id": session})
