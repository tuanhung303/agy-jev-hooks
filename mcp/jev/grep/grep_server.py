"""stdio MCP server with one tool, jev_grep (see grep_tool).

Every call runs at once on its own thread: no queue, no BUSY. A cancelled call sends nothing.
Paths are shown relative to the base folder when the target is within it, otherwise relative to the
target root. Each call logs one JSON line (no code) to
~/.local/state/agy-jev-hooks/jev-grep/events.jsonl.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import grep_tool

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
STATE_DIR = Path(os.environ.get("JEV_GREP_STATE") or Path.home() / ".local/state/agy-jev-hooks/jev-grep")


def base_folder(env=None):
    env = os.environ if env is None else env
    for candidate in (env.get("JEV_GREP_ROOT"), env.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if candidate and os.path.isabs(candidate) and os.path.isdir(candidate):
            return os.path.realpath(candidate)
    return os.path.realpath(os.getcwd())


def log_event(event):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(STATE_DIR / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": stamp, **event}) + "\n")
    except OSError:
        pass


def serve(input_stream=sys.stdin, output=sys.stdout, base=None, run=grep_tool.run, log=log_event):
    base = base or base_folder()
    write_lock = threading.Lock()
    calls = {}  # request id key -> cancel event
    threads = []

    def send(message):
        with write_lock:
            output.write(json.dumps(message, separators=(",", ":")) + "\n")
            output.flush()

    def reply(call_id, result=None, error=None):
        send({"jsonrpc": "2.0", "id": call_id, **({"error": error} if error else {"result": result})})

    def execute(call_id, arguments, cancel):
        started = time.monotonic()
        try:
            text, event = run(arguments, base, cancel_event=cancel, started=started)
            result = {"content": [{"type": "text", "text": text}], "isError": event.get("outcome") == "rg_error"}
        except grep_tool.ToolInputError as cause:
            text, event = str(cause), {"outcome": "bad_input"}
            result = {"content": [{"type": "text", "text": text}], "isError": True}
        except Exception as cause:  # noqa: BLE001 - the agent gets a short error, never a traceback
            event = {"outcome": "error", "error": type(cause).__name__}
            result = {"content": [{"type": "text", "text": "jev_grep failed; use rg instead."}], "isError": True}
        event.setdefault("elapsed_s", round(time.monotonic() - started, 2))
        log({**event, "base": base})
        key = json.dumps(call_id)
        if not cancel.is_set():
            reply(call_id, result)
        calls.pop(key, None)

    def handle(message):
        call_id, method = message.get("id"), message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            reply(call_id, {
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "jevgrep", "version": "2"},
                "instructions": f"One tool: {grep_tool.TOOL_NAME}, rg ranked for your task. Base folder: {base}",
            })
        elif method == "ping":
            reply(call_id, {})
        elif method == "tools/list":
            reply(call_id, {"tools": [{
                "name": grep_tool.TOOL_NAME, "description": grep_tool.TOOL_DESCRIPTION,
                "inputSchema": grep_tool.TOOL_INPUT_SCHEMA,
                "annotations": {"readOnlyHint": True, "openWorldHint": True, "title": "rg ranked by Jev"},
            }]})
        elif method == "tools/call":
            if params.get("name") != grep_tool.TOOL_NAME:
                reply(call_id, error={"code": -32602, "message": "unknown tool"})
                return
            cancel = threading.Event()
            calls[json.dumps(call_id)] = cancel
            thread = threading.Thread(target=execute, args=(call_id, params.get("arguments") or {}, cancel),
                                      daemon=True)
            threads.append(thread)
            thread.start()
        elif method == "notifications/cancelled":
            cancel = calls.get(json.dumps(params.get("requestId")))
            if cancel is not None:
                cancel.set()
        elif call_id is not None and method:
            reply(call_id, error={"code": -32601, "message": "unsupported method"})

    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}})
            continue
        if isinstance(message, dict):
            handle(message)
    for thread in threads:
        thread.join(timeout=30)


def main():
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
