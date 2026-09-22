#!/usr/bin/env python3
"""Fork a Qoder session while keeping the source session's own model.

A fork inherits the parent history, so reusing the parent model lets the
provider reuse its prompt cache for that shared prefix. Fork right after the
parent turn, inside the provider cache TTL (5 minutes by default); a different
model pays full input for the replayed history.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECTS_DIR = Path.home() / ".qoder" / "projects"
CACHE_TTL_SECONDS = 300
APP_SDK_ENV = (
    "QODER_AGENT_SDK_ENTRYPOINT",
    "QODER_SDK_AUTH_PAYLOAD_FILE",
    "QODER_SESSION_TYPE",
    "QODER_WORKER_CWD",
    "QODER_WORKER_RUNTIME_ASSET_ROOT",
)


def slug(path):
    return "".join(c if c.isalnum() else "-" for c in str(path))


def find_session(cwd, session_id):
    session_dir = PROJECTS_DIR / slug(Path(cwd).resolve())
    if session_id:
        candidate = session_dir / f"{session_id}.jsonl"
        if not candidate.is_file():
            sys.exit(f"no session {session_id} under {session_dir}")
        return candidate
    files = sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.exit(f"no sessions under {session_dir}")
    return files[0]


def read_model(path):
    model = None
    fallback = None
    with path.open() as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "runtime-config" and entry.get("model"):
                model = entry["model"]
            elif entry.get("type") == "assistant" and fallback is None:
                fallback = entry.get("message", {}).get("model")
    recorded = model or fallback
    if not recorded:
        sys.exit(f"no model recorded in {path}")
    return recorded


def qodercli_path():
    found = shutil.which("qodercli")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "qodercli"
    if local.is_file():
        return str(local)
    sys.exit("qodercli not found on PATH")


def parse_result(stdout):
    payload = None
    for line in stdout.splitlines():
        if '"type":"result"' not in line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if candidate.get("type") == "result":
            payload = candidate
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", help="prompt to run inside the fork")
    parser.add_argument("--session", help="source session id (default: most recent session for --cwd)")
    parser.add_argument("--cwd", default=os.getcwd(), help="working directory (default: current directory)")
    parser.add_argument("--model", help="override the model instead of reusing the source session's")
    parser.add_argument("--json", action="store_true", help="print the raw qodercli result JSON")
    parser.add_argument("--dry-run", action="store_true", help="print the command without running it")
    args = parser.parse_args()

    source = find_session(args.cwd, args.session)
    model = args.model or read_model(source)

    age = time.time() - source.stat().st_mtime
    if not args.model and age > CACHE_TTL_SECONDS:
        print(f"note: source last written {int(age)}s ago; prompt cache likely cold", file=sys.stderr)

    command = [
        qodercli_path(),
        "-w", args.cwd,
        "-r", source.stem,
        "--fork-session",
        "-m", model,
        "-o", "json",
        "-p", args.prompt,
    ]
    if args.dry_run:
        print(" ".join(command))
        print(f"# source={source.name} model={model}")
        return

    env = {k: v for k, v in os.environ.items() if k not in APP_SDK_ENV}
    completed = subprocess.run(command, capture_output=True, text=True, env=env)
    payload = parse_result(completed.stdout)
    if payload is None:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        sys.exit(f"no result from qodercli (exit {completed.returncode})")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"fork: {payload.get('session_id')}")
        print(f"model: {model}")
        print(f"result: {payload.get('result')}")
    if payload.get("is_error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
