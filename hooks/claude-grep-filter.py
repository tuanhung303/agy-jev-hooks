#!/usr/bin/env python3
"""PostToolUse filter for Claude Code's Grep, and for plain `rg` / `grep` run
through Bash: Jev scores the code around each match against the current task,
and Claude sees the relevant files only.

Grep finds candidates locally; Jev only evaluates fragments that contain a
match, under the same JevGrep profile, ignore rules and credential quarantine
as semantic_search_code. Fail open: any error, a partial scan, an unauthorized
path or a result nobody scored above threshold returns the original output.
A Bash command is filtered only when the hook can mirror it exactly: one
search command, optionally after `cd DIR &&`, no pipes or redirects, known
flags only, and output large enough to be worth the wait.
Repeating the identical Grep call or command in a session returns it unfiltered.

Modes (CLAUDE_GREP_FILTER_MODE): `shadow` (default) ranks and logs only, and is
meant to run as an async hook so the search never waits; `hint` adds Jev's top
files as context and hides nothing; `filter` hides low-scored files; `off`.
The query is the user's recent prompts plus what the call says it is for (the
Bash description, or the assistant's text before the call when the transcript
has it), and Jev reads only the lines around each match.
"""
import hashlib
import json
import os
import re
import shlex
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
MODE = os.environ.get("CLAUDE_GREP_FILTER_MODE", "shadow")  # off | shadow | hint | filter
MIN_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_MIN_FILES", "8"))
MAX_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_MAX_FILES", "60"))  # beyond: too broad, pass through at once
MAX_BYTES = int(os.environ.get("CLAUDE_GREP_FILTER_MAX_BYTES", "600000"))
BUDGET_S = float(os.environ.get("CLAUDE_GREP_FILTER_BUDGET_S", "60"))
# Bash output below this is cheaper to read than the Jev round trip (5-15 s) it would cost.
# A hint is worth an interruption only when it names a relevant file the agent did not see, or
# orders a list long enough that the order matters.
HINT_MIN_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_HINT_MIN_FILES", "15"))
# rewake: run as an async hook with asyncRewake; the hint reaches Claude when Jev answers (exit 2).
DELIVERY = os.environ.get("CLAUDE_GREP_FILTER_DELIVERY", "context")  # context | rewake
BASH_MIN_BYTES = int(os.environ.get("CLAUDE_GREP_FILTER_BASH_MIN_BYTES", "4000"))
MAX_SCOPE_ENTRIES, MAX_SCOPE_BYTES, MAX_QUERY_BYTES = 32, 4096, 8192  # request contract limits
TASK_CHARS, STEP_CHARS, RECENT_PROMPTS = 1500, 600, 3
SNIPPET_LINES = int(os.environ.get("CLAUDE_GREP_FILTER_SNIPPET_LINES", "12"))  # context kept around matches
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


def agent_transcript(payload):
    """The transcript that holds this call. Inside a subagent, hook input's transcript_path is the main
    session's; the subagent's own one (its task, its steps) is <session>/subagents/agent-<agent_id>.jsonl."""
    path, agent = payload.get("transcript_path"), str(payload.get("agent_id") or "")
    if path and re.fullmatch(r"[\w-]+", agent):
        candidate = os.path.join(re.sub(r"\.jsonl$", "", path), "subagents", f"agent-{agent}.jsonl")
        if os.path.isfile(candidate):
            return candidate
    return path


def task_from_transcript(path, tool_use_id=None):
    """The user's last few genuine prompts (newest last) and the assistant text that led to this call.
    One prompt is often a bare "yes, do it"; the ones before it carry what the work is about."""
    prompts, step = [], ""
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
                prompts, step = prompts[-(RECENT_PROMPTS - 1):] + [text], ""
        elif entry.get("type") == "assistant":
            text = _text_of(content).strip()
            if text:
                step = text
            if tool_use_id and isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("id") == tool_use_id for b in content):
                break
    return "\n\n".join(prompts)[-TASK_CHARS:], step[-STEP_CHARS:]


def build_query(task, step, pattern, purpose=""):
    parts = [f"Recent user requests, newest last: {task}" if task else "",
             f"What this search is for: {purpose}" if purpose else "",
             f"Current step: {step}" if step else "",
             f"Which code matching the grep pattern {pattern!r} is relevant to this task?"]
    query = "\n".join(part for part in parts if part)
    # The contract bounds UTF-8 bytes, not characters (Vietnamese prompts are multi-byte).
    return query.encode("utf-8")[:MAX_QUERY_BYTES].decode("utf-8", "ignore")


def grep_tool_args(tool_input):
    """rg flags reproducing the Grep tool call's pattern and filters."""
    args = []
    if tool_input.get("-i"):
        args.append("--ignore-case")
    if tool_input.get("multiline"):
        args += ["--multiline", "--multiline-dotall"]
    if tool_input.get("glob"):
        args += ["--glob", str(tool_input["glob"])]
    if tool_input.get("type"):
        args += ["--type", str(tool_input["type"])]
    return args + ["-e", str(tool_input.get("pattern", ""))]


# Flags that change which lines match, forwarded to rg as they are.
RG_MATCH_SWITCHES = {"-i", "--ignore-case", "-w", "--word-regexp", "-F", "--fixed-strings", "-x", "--line-regexp",
                     "-P", "--pcre2", "-S", "--smart-case", "--hidden", "--no-ignore", "-u", "-uu", "-uuu",
                     "-L", "--follow"}
GREP_MATCH_SWITCHES = {"-i": "--ignore-case", "--ignore-case": "--ignore-case", "-w": "--word-regexp",
                       "--word-regexp": "--word-regexp", "-F": "--fixed-strings", "--fixed-strings": "--fixed-strings",
                       "-x": "--line-regexp", "--line-regexp": "--line-regexp", "-P": "--pcre2"}
# Flags that only change how lines are printed, never which files match or whether paths are shown.
# The same letter means different things to rg and grep (rg -r is --replace, -I is --no-filename).
OUTPUT_SWITCHES = {
    "rg": {"-n", "--line-number", "-H", "--with-filename", "--no-heading", "-N", "--no-line-number",
           "-l", "--files-with-matches", "-o", "--only-matching", "-s", "--case-sensitive", "--no-messages",
           "--color", "--colour", "-A", "-B", "-C", "--after-context", "--before-context", "--context",
           "-m", "--max-count"},
    "grep": {"-n", "--line-number", "-H", "--with-filename", "-l", "--files-with-matches", "-o", "--only-matching",
             "-I", "-s", "--no-messages", "-r", "-R", "--recursive", "-E", "--extended-regexp", "--color", "--colour",
             "-A", "-B", "-C", "--after-context", "--before-context", "--context", "-m", "--max-count"},
}
VALUE_FLAGS = {
    "rg": {"-A", "-B", "-C", "--after-context", "--before-context", "--context", "-m", "--max-count",
           "-e", "--regexp", "-g", "--glob", "-t", "--type", "-T", "--type-not", "--color", "--colour"},
    "grep": {"-A", "-B", "-C", "--after-context", "--before-context", "--context", "-m", "--max-count",
             "-e", "--regexp", "--include", "--exclude", "--exclude-dir"},
}
SHELL_OPERATORS = set("|&;<>()")
STDERR_TO_NULL = re.compile(r"\s2>\s*/dev/null(?=[\s|]|$)")
# `grep ... | head -20` is how agents usually search; the match set is the same, only the view is cut.
HEAD_TAIL = re.compile(r"\s*\|\s*head(?:\s+-n\s*\d+|\s+-\d+|\s+--lines=\d+)?\s*$")
CD_PREFIX = re.compile(r"""\s*cd\s+("[^"]*"|'[^']*'|\S+)\s*&&\s*""")


def bre_to_rg(pattern):
    """grep's default basic regex in rg syntax: \\| \\( \\) \\{ \\} \\+ \\? are the operators, bare ones literals."""
    out, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            following = pattern[index + 1]
            out.append(following if following in "|(){}+?" else char + following)
            index += 2
            continue
        out.append("\\" + char if char in "|(){}+?" else char)
        index += 1
    return "".join(out)


def _split_flags(argv, value_flags):
    """Expand bundled short flags (-rn, -A3, -tpy) and --flag=value into (flag, value) pairs and positionals."""
    flags, positionals, index = [], [], 0
    while index < len(argv):
        token = argv[index]
        index += 1
        if token == "--":
            positionals += argv[index:]
            break
        if token.startswith("--"):
            name, eq, value = token.partition("=")
            if name in value_flags and not eq:
                if index >= len(argv):
                    return None
                value, index = argv[index], index + 1
            flags.append((name, value if (eq or name in value_flags) else None))
        elif token.startswith("-") and len(token) > 1:
            if token in ("-uu", "-uuu"):
                flags.append((token, None))
                continue
            for position in range(1, len(token)):
                name = "-" + token[position]
                if name in value_flags:
                    value = token[position + 1:]
                    if not value:
                        if index >= len(argv):
                            return None
                        value, index = argv[index], index + 1
                    flags.append((name, value))
                    break
                flags.append((name, None))
        else:
            positionals.append(token)
    return flags, positionals


def bash_search(command, cwd):
    """(rg args, search paths, output cwd, pattern, truncated) for a plain rg / grep command, optionally
    cut by a trailing `| head`, else None. Anything else the hook cannot mirror exactly (chains, other
    pipes, redirects, unknown flags) is left alone."""
    text = STDERR_TO_NULL.sub(" ", command or "")
    head = HEAD_TAIL.search(text)
    if head:
        text = text[:head.start()]
    prefix = CD_PREFIX.match(text)
    try:
        if prefix:
            cwd = os.path.realpath(os.path.join(cwd, os.path.expanduser(shlex.split(prefix.group(1))[0])))
            text = text[prefix.end():]
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        argv = list(lexer)
    except ValueError:
        return None
    if not argv or any(token and set(token) <= SHELL_OPERATORS for token in argv):
        return None
    binary = os.path.basename(argv[0])
    if binary not in ("rg", "grep", "egrep", "fgrep"):
        return None
    is_grep = binary != "rg"
    family = "grep" if is_grep else "rg"
    parsed = _split_flags(argv[1:], VALUE_FLAGS[family])
    if parsed is None:
        return None
    flags, positionals = parsed
    # grep reads every file under the path; rg would skip ignored and hidden ones.
    args, patterns = (["--no-ignore", "--hidden"] if is_grep else []), []
    regex = {"egrep": "ere", "fgrep": "fixed"}.get(binary, "bre" if is_grep else "rust")
    for name, value in flags:
        if name in ("-e", "--regexp"):
            patterns.append(value)
        elif name in ("-g", "--glob", "-t", "--type", "-T", "--type-not") and not is_grep:
            args += [name, value]
        elif name == "--include" and is_grep:
            args += ["--glob", value]
        elif name in ("--exclude", "--exclude-dir") and is_grep:
            args += ["--glob", "!" + value]
        elif is_grep and name in GREP_MATCH_SWITCHES:
            args.append(GREP_MATCH_SWITCHES[name])
            regex = {"-F": "fixed", "--fixed-strings": "fixed", "-P": "pcre"}.get(name, regex)
        elif is_grep and name in ("-E", "--extended-regexp"):
            regex = "ere" if regex == "bre" else regex
        elif not is_grep and name in RG_MATCH_SWITCHES:
            args.append(name)
        elif name in OUTPUT_SWITCHES[family]:
            continue
        else:
            return None  # a flag we cannot mirror (-v, -c, -h, --heading, ...): leave the output alone
    if not patterns:
        if not positionals:
            return None
        patterns, positionals = [positionals[0]], positionals[1:]
    if regex == "bre":
        patterns = [bre_to_rg(pattern) for pattern in patterns]
    elif regex == "fixed" and "--fixed-strings" not in args:
        args.append("--fixed-strings")
    for pattern in patterns:
        args += ["-e", pattern]
    paths = [os.path.realpath(os.path.join(cwd, os.path.expanduser(path))) for path in positionals] or [cwd]
    return args, paths, cwd, patterns[0], bool(head)


def grep_matches(search_args, search_paths):
    """(absolute path, line) pairs for the given rg pattern and filters."""
    command = ["rg", "--line-number", "--no-heading", "--with-filename", "--color", "never",
               *search_args, "--", *search_paths]
    output = subprocess.run(command, capture_output=True, timeout=15).stdout.decode("utf-8", "replace")
    matches = defaultdict(set)
    for line in output.splitlines():
        found = re.match(r"(.+?):(\d+):", line)
        if found:
            matches[found.group(1)].add(int(found.group(2)))
    return matches


def snippet_mapper(lines, radius):
    """Trim each candidate fragment to the span from `radius` lines before its first match to
    `radius` after its last, and drop fragments with no match or the same matches as an
    overlapping neighbour. Jev reads less unrelated text (its docs: filter first) and less is sent."""
    from dataclasses import replace
    from mcp.jev.grep.tokens import count_reference_tokens
    seen = set()

    def trim(fragment):
        found = sorted(n for n in lines.get(fragment.path, ()) if fragment.start_line <= n <= fragment.end_line)
        if not found or (fragment.path, tuple(found)) in seen:
            return None
        seen.add((fragment.path, tuple(found)))
        start = max(fragment.start_line, found[0] - radius)
        end = min(fragment.end_line, found[-1] + radius)
        rows = re.findall(r"[^\n]*\n|[^\n]+$", fragment.text)
        skip = start - fragment.start_line
        text = "".join(rows[skip:end - fragment.start_line + 1])
        byte_start = fragment.byte_start + len("".join(rows[:skip]).encode("utf-8"))
        size = len(text.encode("utf-8"))
        return replace(fragment, start_line=start, end_line=end, text=text, byte_start=byte_start,
                       byte_end=byte_start + size, byte_count=size, token_count=count_reference_tokens(text))
    return trim


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
    kept = _drop_orphan_separators(
        [line for line in str(response.get("content") or "").split("\n")
         if not any(line.startswith(path + ":") or line.startswith(path + "-") for path in hide_paths)])
    updated["content"] = "\n".join(kept)
    if "numLines" in updated:
        updated["numLines"] = len(kept)
    return updated


def _shown(absolute, cwd):
    """A path as the agent would type it: relative inside the working directory, absolute outside it."""
    relative = os.path.relpath(absolute, cwd)
    return absolute if relative.startswith("..") else relative


def _drop_orphan_separators(kept):
    """Drop context separators left leading, trailing or doubled by a hidden file."""
    kept = [line for index, line in enumerate(kept) if line != "--" or (kept[index - 1:index] != ["--"] and index > 0)]
    while kept and kept[-1] == "--":
        kept.pop()
    return kept


def line_owner(known_files, cwd):
    """Resolve an output line to the matched file it belongs to (absolute), or None. rg and grep print
    a path as given (relative, ./-prefixed or absolute), so each line goes to the longest matched file
    it starts with: a line of `a.py-old` never counts as a context line of `a.py`."""
    known = {os.path.normpath(path) for path in known_files}

    def owner(line):
        whole = os.path.normpath(os.path.join(cwd, line))
        if whole in known:  # -l output: the line is the path
            return whole
        found = None
        for index, char in enumerate(line[:4096]):
            if index and char in ":-":
                candidate = os.path.normpath(os.path.join(cwd, line[:index]))
                found = candidate if candidate in known else found
        return found
    return owner


def visible_files(response, known_files, cwd):
    """The matched files the agent actually saw in the (possibly head-cut) output."""
    if "stdout" in response:
        text = str(response.get("stdout") or "")
    elif response.get("mode") == "files_with_matches":
        text = "\n".join(str(name) for name in response.get("filenames") or [])
    else:
        text = str(response.get("content") or "")
    owner = line_owner(known_files, cwd)
    return {owner(line) for line in text.split("\n")} - {None}


def filtered_bash_response(response, hide_files, known_files, cwd):
    """Bash output without the lines of hide_files (see line_owner for how a line finds its file)."""
    hide = {os.path.normpath(path) for path in hide_files}
    owner = line_owner(set(known_files) | hide, cwd)

    def hidden(line):
        return owner(line) in hide

    stdout = str(response.get("stdout") or "")
    lines = stdout.split("\n")
    trailing = lines[-1] == ""
    kept = _drop_orphan_separators([line for line in (lines[:-1] if trailing else lines) if not hidden(line)])
    return {**response, "stdout": "\n".join(kept) + ("\n" if trailing and kept else "")}


def run(payload):
    started = time.time()
    event = {"session": payload.get("session_id"), "mode": MODE}
    tool_input = payload.get("tool_input") or {}
    response = payload.get("tool_response") or {}
    tool_name = payload.get("tool_name")
    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    if MODE == "off":
        return None, {**event, "outcome": "not_applicable"}
    if tool_name == "Grep" and response.get("mode") in HANDLED_MODES:
        search_paths = [os.path.realpath(os.path.join(cwd, tool_input.get("path") or "."))]
        search_args, pattern = grep_tool_args(tool_input), tool_input.get("pattern")
    elif tool_name == "Bash":
        search = bash_search(tool_input.get("command"), cwd)
        if search is None or response.get("interrupted") or not isinstance(response.get("stdout"), str):
            return None, {**event, "outcome": "not_applicable", "quiet": True}
        search_args, search_paths, cwd, pattern, truncated = search
        event["tool"] = "Bash"
        if truncated:
            event["truncated"] = True  # the agent saw only the head: ranking the rest is the point
        elif len(response["stdout"].encode("utf-8")) < BASH_MIN_BYTES:
            return None, {**event, "outcome": "small_output"}
        purpose = str(tool_input.get("description") or "")
        # Only the command identifies a repeat; Claude rewrites the description freely.
        payload = {**payload, "tool_input": {"command": tool_input.get("command")}}
    else:
        return None, {**event, "outcome": "not_applicable"}
    tool_input = {"pattern": pattern, "purpose": purpose if tool_name == "Bash" else ""}

    search_path = search_paths[0] if len(search_paths) == 1 else os.path.commonpath(search_paths)
    matches = grep_matches(search_args, search_paths)
    event["matched_files"] = len(matches)
    if len(matches) < MIN_FILES:
        return None, {**event, "outcome": "few_files"}
    if len(matches) > MAX_FILES:
        note = (f"Jev grep filter: {len(matches)} files match, too many to rank quickly; output unchanged. "
                "Pass a narrower path to Grep to get relevance filtering.")
        output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}
        return (output if MODE == "filter" else None), {**event, "outcome": "too_broad"}
    claimed = MODE == "filter"  # only hiding needs the repeat bypass
    if claimed and not _claim(payload):
        return None, {**event, "outcome": "repeat_unfiltered"}
    output, event = None, {**event, "outcome": "error"}
    try:
        output, event = _rank(payload, event, tool_input, response, cwd, search_path, matches, started)
    finally:
        if claimed and event.get("outcome") != "filtered":
            _release(payload)  # only a filtered call arms the repeat bypass
    return output, event


def _rank(payload, event, tool_input, response, cwd, search_path, matches, started):

    from mcp.jev.grep.config import ConfigurationError, find_profile_for, load_configuration
    from mcp.jev.grep.engine import SearchEngine
    from mcp.jev.grep.prepare import find_credential_pattern

    transcript = agent_transcript(payload)
    task, step = task_from_transcript(transcript, payload.get("tool_use_id"))
    query = build_query(task, step, str(tool_input.get("pattern", "")), tool_input.get("purpose", ""))
    event.update({"tool_use_id": payload.get("tool_use_id"), "transcript": transcript,
                  "agent_type": payload.get("agent_type"),
                  "intent": "description" if tool_input.get("purpose") else ("step" if step else "prompts")})
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

    result = SearchEngine(loaded, env=dict(os.environ)).search(
        # The response stays in-process, so reserve the largest budget: the report
        # envelope alone grows with the scope list.
        {"query": query, "scope": cover(lines), "max_context_tokens": config["search"]["max_response_tokens"]},
        {"fragment_map": snippet_mapper(lines, SNIPPET_LINES)})
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
    best, span = {}, {}
    for path, start, end, score in result.get("fragment_scores") or ():
        if score > best.get(path, -1.0):
            best[path], span[path] = score, (start, end)  # the file's best-scored lines
    if not best:  # every candidate was outside the profile (gitignored, denied) or unreadable
        return None, {**event, "outcome": "nothing_evaluated"}
    ranked = sorted(best, key=lambda p: -best[p])
    relevant = [path for path in ranked if best[path] >= threshold]
    # Hide only what Jev scored low; anything it never evaluated stays visible.
    hidden = [path for path in lines if path in best and best[path] < threshold]
    event.update({"kept_files": len(relevant), "hidden_files": len(hidden),
                  "ranked": [[path, round(best[path], 2)] for path in ranked[:10]]})
    if MODE == "shadow":
        return None, {**event, "outcome": "shadow_ranked"}
    as_shown = {path: _shown(os.path.join(root, path), cwd) for path in lines}
    if MODE == "hint":
        if not relevant:
            return None, {**event, "outcome": "none_relevant"}
        seen = visible_files(response, [os.path.join(root, p) for p in lines], cwd)
        unseen = [p for p in relevant[:3] if os.path.normpath(os.path.join(root, p)) not in seen]
        event["unseen_top"] = len(unseen)
        if not unseen and len(best) < HINT_MIN_FILES:
            return None, {**event, "outcome": "hint_not_needed"}
        top = relevant[:8]
        # Print the shared folder once: long absolute paths were over half of the hint's tokens.
        base = os.path.commonpath([os.path.dirname(as_shown[p]) or "." for p in top]) if len(top) > 1 else ""
        base = "" if base in ("", ".", "/") else base

        def entry(p):
            shown = os.path.relpath(as_shown[p], base) if base else as_shown[p]
            return f"{shown}:{span[p][0]}-{span[p][1]} ({best[p]:.2f}{', not in your output' if p in unseen else ''})"
        note = (f"Jev hint for the search {tool_input.get('pattern')!r} ({len(best)} files). Most relevant lines, "
                f"read these first{f' (under {base}/)' if base else ''}: " + ", ".join(entry(p) for p in top)
                + (f". {len(hidden)} more scored below {threshold}" if hidden else "") + ". Nothing was hidden.")
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}, \
            {**event, "outcome": "hinted"}
    if not relevant:
        note = (f"Jev grep filter: none of the {len(best)} evaluated files matching "
                f"{tool_input.get('pattern')!r} scored as relevant to the task; output unchanged.")
        output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}
        return (output if MODE == "filter" else None), {**event, "outcome": "none_relevant"}

    if not hidden:
        return None, {**event, "outcome": "all_relevant"}

    by_dir = Counter(path.split("/")[0] for path in hidden)
    note = (f"Jev grep filter: kept {len(relevant)} of {len(best)} evaluated files matching "
            f"{tool_input.get('pattern')!r} as relevant to the task ("
            + ", ".join(f"{as_shown[p]} {best[p]:.2f}" for p in relevant[:8])
            + f"). Hidden {len(hidden)} low-relevance files"
            + (f" (by top directory: {', '.join(f'{k} {v}' for k, v in by_dir.most_common(5))})" if by_dir else "")
            + (". To see every match, rerun the identical command." if payload.get("tool_name") == "Bash"
               else ". To see every match, repeat the identical Grep call."))
    if payload.get("tool_name") == "Bash":
        updated = filtered_bash_response(response, {os.path.join(root, p) for p in hidden}, matches, cwd)
    else:
        updated = filtered_response(response, {as_shown[p] for p in hidden})
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": updated,
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
    if not event.pop("quiet", False):  # every non-search Bash call lands here; keep the log to searches
        _log(event)
    specific = (output or {}).get("hookSpecificOutput") or {}
    if DELIVERY == "rewake" and specific.get("additionalContext") and "updatedToolOutput" not in specific:
        sys.stderr.write(specific["additionalContext"] + "\n")  # asyncRewake hands exit-2 stderr to Claude
        return 2
    print(json.dumps(output or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
