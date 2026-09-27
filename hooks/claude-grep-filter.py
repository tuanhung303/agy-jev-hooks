#!/usr/bin/env python3
"""PostToolUse filter for Claude Code's Grep: Jev scores the code around each
match against the current task, and Claude sees the relevant files only.

Grep finds candidates locally; Jev only evaluates fragments that contain a
match, under the same JevGrep profile, ignore rules and credential quarantine
as semantic_search_code. Fail open: any error, a partial scan, an unauthorized
path or a result nobody scored above threshold returns the original output.
Repeating the identical Grep call in a session returns it unfiltered.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

STATE_DIR = Path(os.environ.get("CLAUDE_GREP_FILTER_STATE") or
                 Path.home() / ".local/state/agy-jev-hooks/claude-grep-filter")
MODE = os.environ.get("CLAUDE_GREP_FILTER_MODE", "filter")  # off | shadow | filter
MIN_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_MIN_FILES", "8"))
MAX_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_MAX_FILES", "60"))  # beyond: too broad, pass through at once
MAX_BYTES = int(os.environ.get("CLAUDE_GREP_FILTER_MAX_BYTES", "600000"))
BUDGET_S = float(os.environ.get("CLAUDE_GREP_FILTER_BUDGET_S", "60"))
MAX_SCOPE_ENTRIES, MAX_SCOPE_BYTES, MAX_QUERY_BYTES = 32, 4096, 8192  # request contract limits
TASK_CHARS, STEP_CHARS = 1500, 600
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
HANDLED_MODES = ("files_with_matches", "content")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(event):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": _now(), **event}) + "\n")
    except OSError:
        pass


def _seen_marker(payload):
    """Marks a Grep call that was filtered; its identical repeat in the session bypasses the filter."""
    key = hashlib.sha256(json.dumps([payload.get("session_id"), payload.get("tool_input")],
                                    sort_keys=True).encode("utf-8")).hexdigest()
    return STATE_DIR / "seen" / key


def _claim(payload):
    """Atomically claim this Grep call. False when an identical call already holds the
    claim: a repeat, or a twin Claude Code runs in parallel. Either stays unfiltered."""
    marker = _seen_marker(payload)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False
    except OSError:
        return True  # no state dir: filter, but a repeat cannot bypass


def _release(payload):
    try:
        _seen_marker(payload).unlink()
    except OSError:
        pass


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def task_from_transcript(path, tool_use_id=None):
    """The last genuine user prompt and the assistant text that led to this call."""
    task = step = ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()[-400:]
    except (OSError, TypeError):
        return task, step
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if entry.get("type") == "user" and not entry.get("isMeta"):
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                 for b in content):
                continue
            text = REMINDER.sub("", _text_of(content)).strip()
            if text and not text.startswith(("<local-command", "<command-name>")):
                task, step = text, ""
        elif entry.get("type") == "assistant":
            text = _text_of(content).strip()
            if text:
                step = text
            if tool_use_id and isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("id") == tool_use_id for b in content):
                break
    return task[-TASK_CHARS:], step[-STEP_CHARS:]


def build_query(task, step, pattern):
    parts = [f"Task: {task}" if task else "", f"Current step: {step}" if step else "",
             f"Which code matching the grep pattern {pattern!r} is relevant to this task?"]
    query = "\n".join(part for part in parts if part)
    # The contract bounds UTF-8 bytes, not characters (Vietnamese prompts are multi-byte).
    return query.encode("utf-8")[:MAX_QUERY_BYTES].decode("utf-8", "ignore")


def grep_matches(tool_input, search_path):
    """(absolute path, line) pairs for the same pattern and filters the Grep tool used."""
    command = ["rg", "--line-number", "--no-heading", "--with-filename", "--color", "never"]
    if tool_input.get("-i"):
        command.append("--ignore-case")
    if tool_input.get("multiline"):
        command += ["--multiline", "--multiline-dotall"]
    if tool_input.get("glob"):
        command += ["--glob", str(tool_input["glob"])]
    if tool_input.get("type"):
        command += ["--type", str(tool_input["type"])]
    command += ["-e", str(tool_input.get("pattern", "")), "--", search_path]
    output = subprocess.run(command, capture_output=True, timeout=15).stdout.decode("utf-8", "replace")
    matches = defaultdict(set)
    for line in output.splitlines():
        found = re.match(r"(.+?):(\d+):", line)
        if found:
            matches[found.group(1)].add(int(found.group(2)))
    return matches


def cover(paths, limit=MAX_SCOPE_ENTRIES, max_bytes=MAX_SCOPE_BYTES):
    """Scope entries covering every path within the contract's entry and byte limits:
    collapse the deepest entries to their parents until both fit."""
    entries = set(paths)
    while len(entries) > limit or sum(len(entry.encode("utf-8")) for entry in entries) > max_bytes:
        deepest = max(entry.count("/") for entry in entries)
        if deepest == 0:
            return ["."]
        entries = {entry.rsplit("/", 1)[0] if entry.count("/") == deepest else entry
                   for entry in entries}
        entries = {entry for entry in entries
                   if not any(entry != other and entry.startswith(other + "/") for other in entries)}
    return sorted(entries)


def filtered_response(response, hide_paths):
    """The Grep output shape Claude Code expects, without hide_paths."""
    updated = dict(response)
    if response.get("mode") == "files_with_matches":
        updated["filenames"] = [name for name in response.get("filenames") or [] if name not in hide_paths]
        updated["numFiles"] = len(updated["filenames"])
        return updated
    kept = [line for line in str(response.get("content") or "").split("\n")
            if not any(line.startswith(path + ":") or line.startswith(path + "-") for path in hide_paths)]
    # Drop separators left leading, trailing or doubled by a hidden file.
    kept = [line for index, line in enumerate(kept) if line != "--" or (kept[index - 1:index] != ["--"] and index > 0)]
    while kept and kept[-1] == "--":
        kept.pop()
    updated["content"] = "\n".join(kept)
    if "numLines" in updated:
        updated["numLines"] = len(kept)
    return updated


def run(payload):
    started = time.time()
    event = {"session": payload.get("session_id"), "mode": MODE}
    tool_input = payload.get("tool_input") or {}
    response = payload.get("tool_response") or {}
    if MODE == "off" or payload.get("tool_name") != "Grep" or response.get("mode") not in HANDLED_MODES:
        return None, {**event, "outcome": "not_applicable"}

    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    search_path = os.path.realpath(os.path.join(cwd, tool_input.get("path") or "."))
    matches = grep_matches(tool_input, search_path)
    event["matched_files"] = len(matches)
    if len(matches) < MIN_FILES:
        return None, {**event, "outcome": "few_files"}
    if len(matches) > MAX_FILES:
        note = (f"Jev grep filter: {len(matches)} files match, too many to rank quickly; output unchanged. "
                "Pass a narrower path to Grep to get relevance filtering.")
        output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}
        return (output if MODE == "filter" else None), {**event, "outcome": "too_broad"}
    if not _claim(payload):
        return None, {**event, "outcome": "repeat_unfiltered"}
    output, event = None, {**event, "outcome": "error"}
    try:
        output, event = _rank(payload, event, tool_input, response, cwd, search_path, matches, started)
    finally:
        if event.get("outcome") != "filtered":
            _release(payload)  # only a filtered call arms the repeat bypass
    return output, event


def _rank(payload, event, tool_input, response, cwd, search_path, matches, started):

    from mcp.jev.grep.config import ConfigurationError, find_profile_for, load_configuration
    from mcp.jev.grep.engine import SearchEngine
    from mcp.jev.grep.prepare import find_credential_pattern

    task, step = task_from_transcript(payload.get("transcript_path"), payload.get("tool_use_id"))
    query = build_query(task, step, str(tool_input.get("pattern", "")))
    if find_credential_pattern(query):
        return None, {**event, "outcome": "query_quarantined"}
    try:
        loaded = load_configuration(find_profile_for(cwd=search_path))
    except ConfigurationError:
        return None, {**event, "outcome": "no_profile"}
    root = loaded["repository_root"]
    lines = {}
    for absolute, found in matches.items():
        relative = os.path.relpath(os.path.realpath(absolute), root)
        if not relative.startswith(".."):
            lines[relative.replace(os.sep, "/")] = found
    if len(lines) < MIN_FILES:
        return None, {**event, "outcome": "few_authorized_files"}

    config = loaded["config"]
    config["search"]["deadline_ms"] = int(BUDGET_S * 1000)
    config["scan_caps"]["transmitted_bytes"] = MAX_BYTES  # refuses before dispatch, never truncates

    def keep(fragment):
        return any(fragment.start_line <= line <= fragment.end_line for line in lines.get(fragment.path, ()))

    result = SearchEngine(loaded, env=dict(os.environ)).search(
        # The response stays in-process, so reserve the largest budget: the report
        # envelope alone grows with the scope list.
        {"query": query, "scope": cover(lines), "max_context_tokens": config["search"]["max_response_tokens"]},
        {"fragment_filter": keep})
    outcome = result["outcome"]
    report = outcome.get("report") or {}
    event.update({"status": outcome.get("status"), "error": (outcome.get("error") or {}).get("code"),
                  "stop_reasons": report.get("stop_reasons"),
                  "fragments": (report.get("fragments") or {}).get("total"),
                  "sent_bytes": (report.get("usage") or {}).get("transmitted_bytes"),
                  "elapsed_s": round(time.time() - started, 1)})
    if outcome.get("status") != "complete":
        return None, {**event, "outcome": "search_" + str(outcome.get("status"))}

    threshold = config["search"]["threshold"]
    best = {}
    for path, _, _, score in result.get("fragment_scores") or ():
        best[path] = max(score, best.get(path, 0.0))
    relevant = sorted((path for path, score in best.items() if score >= threshold), key=lambda p: -best[p])
    # Hide only what Jev scored low; anything it never evaluated stays visible.
    hidden = [path for path in lines if path in best and best[path] < threshold]
    event.update({"kept_files": len(relevant), "hidden_files": len(hidden)})
    if not relevant:
        note = (f"Jev grep filter: none of the {len(best)} evaluated files matching "
                f"{tool_input.get('pattern')!r} scored as relevant to the task; output unchanged.")
        output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}
        return (output if MODE == "filter" else None), {**event, "outcome": "none_relevant"}

    if not hidden:
        return None, {**event, "outcome": "all_relevant"}

    as_shown = {path: os.path.relpath(os.path.join(root, path), cwd) for path in lines}
    by_dir = Counter(path.split("/")[0] for path in hidden)
    note = (f"Jev grep filter: kept {len(relevant)} of {len(best)} evaluated files matching "
            f"{tool_input.get('pattern')!r} as relevant to the task ("
            + ", ".join(f"{as_shown[p]} {best[p]:.2f}" for p in relevant[:8])
            + f"). Hidden {len(hidden)} low-relevance files"
            + (f" (by top directory: {', '.join(f'{k} {v}' for k, v in by_dir.most_common(5))})" if by_dir else "")
            + ". To see every match, repeat the identical Grep call.")
    if MODE != "filter":
        return None, {**event, "outcome": "shadow_would_filter"}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "updatedToolOutput": filtered_response(response, {as_shown[p] for p in hidden}),
                                   "additionalContext": note}}, {**event, "outcome": "filtered"}


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        print("{}")
        return 0
    try:
        output, event = run(payload)
    except Exception as cause:  # fail open: grep output stays as it was
        output, event = None, {"session": payload.get("session_id"), "outcome": "error",
                               "error": type(cause).__name__}
    _log(event)
    print(json.dumps(output or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
