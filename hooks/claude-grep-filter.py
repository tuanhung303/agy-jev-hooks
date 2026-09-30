#!/usr/bin/env python3
"""PostToolUse hint for Claude Code's Grep, and for plain `rg` / `grep` run
through Bash: Jev scores the code around each match against the current task,
and Claude is told which files and lines to read first. Nothing is hidden: Jev
puts the answer in its top 10 about 60% of the time, too often wrong to drop
output on (a live filter hid the file the agent needed; see
archive/grep-filter-hide-mode/).

Grep finds candidates locally; Jev only evaluates the lines around each match,
under the same JevGrep profile, ignore rules and credential quarantine as
semantic_search_code. Any error, a partial scan or an unauthorized path yields
no hint. A Bash command is ranked only when the hook can mirror it exactly: one
search command, optionally after `cd DIR &&` and before `| head`, known flags
only. Jev is called only when a hint can help: some matched file is missing
from what the agent saw (cut by head or a limit). The hint names only relevant
files the agent did not see: it judges the ones on screen itself.

Run it as an async hook with asyncRewake: the search never waits, and the hint
reaches Claude as exit-2 stderr when Jev answers (1-3 s later). A hint that
lands after the turn ended is dropped: waking the agent for a finished search
costs a whole turn and the agent ignores it.
Modes (CLAUDE_GREP_FILTER_MODE): `shadow` (default) ranks and logs only, `hint`
also tells Claude, `off`. The query is the user's recent prompts plus the Bash
description of what the search is for.
"""
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from mcp.jev.grep.grep_rank import build_query, cover, rg_matches, snippet_mapper  # noqa: E402

STATE_DIR = Path(os.environ.get("CLAUDE_GREP_FILTER_STATE") or
                 Path.home() / ".local/state/agy-jev-hooks/claude-grep-filter")
MODE = os.environ.get("CLAUDE_GREP_FILTER_MODE", "shadow")  # off | shadow | hint
MIN_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_MIN_FILES", "8"))
MAX_FILES = int(os.environ.get("CLAUDE_GREP_FILTER_MAX_FILES", "60"))  # beyond: too broad, pass through at once
MAX_BYTES = int(os.environ.get("CLAUDE_GREP_FILTER_MAX_BYTES", "600000"))
BUDGET_S = float(os.environ.get("CLAUDE_GREP_FILTER_BUDGET_S", "60"))
TASK_CHARS, RECENT_PROMPTS = 1500, 3
# Context kept around matches, and snippets scored per file. Jev's time grows with the bytes sent (gateway
# requests hold 16 snippets / 64 KB); the file's best snippet decides its rank anyway.
SNIPPET_LINES = int(os.environ.get("CLAUDE_GREP_FILTER_SNIPPET_LINES", "12"))
SNIPPETS_PER_FILE = int(os.environ.get("CLAUDE_GREP_FILTER_SNIPPETS_PER_FILE", "2"))
CONCURRENCY = int(os.environ.get("CLAUDE_GREP_FILTER_CONCURRENCY", "32"))  # parallel Jev requests
TURN_TAIL_BYTES = 4 << 20  # transcript tail searched for the end of the search's turn
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


def task_from_transcript(path):
    """The user's last few genuine prompts, newest last. One prompt is often a bare "yes, do it";
    the ones before it carry what the work is about. The assistant's text before the call is not
    used: Claude Code writes it to the transcript after PostToolUse hooks run, so what the hook
    would find there is an older, stale step."""
    prompts = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()[-400:]
    except (OSError, TypeError):
        return []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") != "user" or entry.get("isMeta"):
            continue
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                             for b in content):
            continue
        text = REMINDER.sub("", _text_of(content)).strip()
        if text and not text.startswith(("<local-command", "<command-name>")):
            prompts = prompts[-(RECENT_PROMPTS - 1):] + [text]
    kept, budget = [], TASK_CHARS  # the newest prompts keep their text first
    for text in reversed(prompts):
        if budget <= 0:
            break
        kept.insert(0, text[-budget:])
        budget -= len(text)
    return kept


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
    """(absolute path -> matched line numbers, rg's error or None) for the given rg pattern and filters."""
    found, error, _ = rg_matches(search_args, search_paths)
    return {path: set(lines) for path, lines in found.items()}, error


def _shown(absolute, cwd):
    """A path as the agent would type it: relative inside the working directory, absolute outside it."""
    relative = os.path.relpath(absolute, cwd)
    return absolute if relative.startswith("..") else relative


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


def run(payload):
    started = time.time()
    event = {"session": payload.get("session_id"), "mode": MODE}
    tool_input = payload.get("tool_input") or {}
    response = payload.get("tool_response") or {}
    tool_name = payload.get("tool_name")
    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    if MODE not in ("shadow", "hint"):
        return None, {**event, "outcome": "not_applicable"}
    purpose = ""
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
            event["truncated"] = True
        purpose = str(tool_input.get("description") or "")
    else:
        return None, {**event, "outcome": "not_applicable"}

    search_path = search_paths[0] if len(search_paths) == 1 else os.path.commonpath(search_paths)
    matches, rg_error = grep_matches(search_args, search_paths)
    if rg_error:  # a bad regex or path: the agent sees rg's own error, there is nothing to rank
        return None, {**event, "outcome": "rg_error", "error": rg_error[:200]}
    event["matched_files"] = len(matches)
    if len(matches) < MIN_FILES:
        return None, {**event, "outcome": "few_files"}
    if len(matches) > MAX_FILES:
        return None, {**event, "outcome": "too_broad"}
    seen = {os.path.realpath(path) for path in visible_files(response, matches, cwd)}
    event["unseen_files"] = len(matches) - len(seen)
    # Every match already on screen: the agent judges those itself, so no hint could come of a Jev call.
    if MODE == "hint" and not event["unseen_files"]:
        return None, {**event, "outcome": "all_shown"}
    return _rank(payload, event, {"pattern": pattern, "purpose": purpose}, seen, cwd, search_path,
                 matches, started)


def turn_ended(transcript, tool_use_id):
    """True once the turn that ran `tool_use_id` is over: a turn-end marker (main session) or an
    end_turn reply (subagent) follows the call in the transcript. A call gone from the transcript's
    tail is long past; one not written yet (short transcript) is still running."""
    if not transcript or not tool_use_id:
        return False
    try:
        with open(transcript, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - TURN_TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    marker = json.dumps(tool_use_id)
    start = next((i for i, line in enumerate(tail) if marker in line), None)
    if start is None:
        return size > TURN_TAIL_BYTES
    for line in tail[start + 1:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "system" and entry.get("subtype") in ("turn_duration", "stop_hook_summary"):
            return True
        if entry.get("type") == "assistant" and (entry.get("message") or {}).get("stop_reason") == "end_turn":
            return True
    return False


def _rank(payload, event, tool_input, seen, cwd, search_path, matches, started):
    from mcp.jev.grep.config import ConfigurationError, find_profile_for, load_configuration
    from mcp.jev.grep.engine import SearchEngine
    from mcp.jev.grep.prepare import find_credential_pattern

    transcript = agent_transcript(payload)
    prompts = task_from_transcript(transcript)
    query = build_query(prompts, str(tool_input.get("pattern", "")), tool_input.get("purpose", ""))
    event.update({"tool_use_id": payload.get("tool_use_id"), "transcript": transcript,
                  "agent_type": payload.get("agent_type"),
                  "intent": "description" if tool_input.get("purpose") else "prompts"})
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
    config["search"]["concurrency"] = CONCURRENCY
    config["scan_caps"]["transmitted_bytes"] = MAX_BYTES  # refuses before dispatch, never truncates

    result = SearchEngine(loaded, env=dict(os.environ)).search(
        # The response stays in-process, so reserve the largest budget: the report
        # envelope alone grows with the scope list.
        {"query": query, "scope": cover(lines), "max_context_tokens": config["search"]["max_response_tokens"]},
        {"fragment_map": snippet_mapper(lines, SNIPPET_LINES, per_file=SNIPPETS_PER_FILE)})
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
    low = len(best) - len(relevant)
    event.update({"kept_files": len(relevant), "low_files": low,
                  "ranked": [[path, round(best[path], 2)] for path in ranked[:10]]})
    if MODE == "shadow":
        return None, {**event, "outcome": "shadow_ranked"}
    if not relevant:
        return None, {**event, "outcome": "none_relevant"}
    unseen = [p for p in relevant if os.path.realpath(os.path.join(root, p)) not in seen]
    event["unseen_relevant"] = len(unseen)
    if not unseen:
        return None, {**event, "outcome": "hint_not_needed"}
    # Plain words on purpose: it lands in the agent's context. Order is the rank; scores stay in the log.
    note = (f"Jev hint: these files match {tool_input.get('pattern')!r} and look relevant to your task, "
            "but your search output did not show them. Read these lines, best first:\n"
            + "\n".join(f"  {_shown(os.path.join(root, p), cwd)}:{span[p][0]}-{span[p][1]}" for p in unseen[:8]))
    if turn_ended(transcript, payload.get("tool_use_id")):
        return None, {**event, "outcome": "late_dropped"}
    return note, {**event, "outcome": "hinted"}


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        print("{}")
        return 0
    try:
        note, event = run(payload)
    except Exception as cause:  # fail quiet: the search result is never touched
        note, event = None, {"session": payload.get("session_id"), "outcome": "error",
                             "error": type(cause).__name__}
    if not event.pop("quiet", False):  # every non-search Bash call lands here; keep the log to searches
        _log(event)
    if note:
        sys.stderr.write(note + "\n")  # exit-2 stderr reaches Claude, at once or via asyncRewake
        return 2
    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
