"""jev_grep: rg finds every match, Jev ranks the matched files against the agent's task.

The reply lists every matching file (root once, then relative paths) with its match lines, and
code only for the best few. Nothing is hidden: a file Jev scores low is still listed. One budget
covers the whole call; when it runs out, files already scored keep their rank and the rest follow
unranked. Jev failing never fails the call. No profile: one defaults file (`defaults.yaml` in the
jevgrep config directory) serves any folder, rooted at the searched path's git repository.
"""
import os
import subprocess
import time

from .grep_rank import build_query, cover, elapsed, rg_matches, snippet_mapper

CODE_FILES = 3  # files that get code in the reply
CODE_LINES = 30  # lines per code block
SMALL_LINES = 40  # at most CODE_FILES files and this many match lines: plain rg output, no Jev
MAX_FILES = 60  # beyond: too broad to rank, list and ask to narrow
MAX_BYTES = 600_000  # bytes Jev may be sent for one call
BUDGET_S = 3.0  # the whole call, rg included
MIN_JEV_S = 0.5  # less left than this after rg: do not start Jev
SNIPPET_LINES, CONCURRENCY = 12, 32  # as the grep hint hook, measured by the bench
LIST_LINES = 6  # match line numbers shown per listed file
TEXT_CHARS = 240  # longest source line shown

TOOL_NAME = "jev_grep"
TOOL_DESCRIPTION = (
    "Regex search like rg, ranked for your task. Finds every file matching `pattern` (ripgrep regex) under "
    "`path`, then a relevance model ranks them against `task` and returns code for the best few files plus every "
    "other matching file with its match lines. Nothing is hidden. Use it for broad or exploratory searches where "
    "many files may match; plain rg is fine for a known symbol or a quick check. The reply starts with the root "
    "it searched; relative paths resolve against the session's start directory, so pass an absolute path after "
    "`cd` or inside a worktree. Takes about 1-3 s."
)
TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regex, ripgrep syntax."},
        "task": {"type": "string", "description": "One sentence: what you are looking for and why."},
        "path": {"type": "string", "description": "File or folder to search, absolute or relative to the root. "
                                                  "Default: the root."},
        "glob": {"type": "string", "description": "ripgrep glob filter, e.g. `*.py` or `!**/test/**`."},
        "ignore_case": {"type": "boolean", "description": "Case-insensitive match."},
    },
    "required": ["pattern", "task"],
    "additionalProperties": False,
}


class ToolInputError(ValueError):
    pass


def repository_root_of(path, base):
    """The git top level holding `path`; outside git, the base folder when it holds `path`, else the
    path's own folder."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if folder == base or folder.startswith(base.rstrip(os.sep) + os.sep):
        fallback = base
    else:
        fallback = folder
    try:
        top = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=2, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    return os.path.realpath(top) if top else fallback


def parse_arguments(arguments, base):
    if not isinstance(arguments, dict):
        raise ToolInputError("arguments must be an object")
    pattern, task = arguments.get("pattern"), arguments.get("task")
    if not isinstance(pattern, str) or not pattern:
        raise ToolInputError("pattern is required")
    if not isinstance(task, str) or not task.strip():
        raise ToolInputError("task is required: one sentence on what you are looking for")
    raw = arguments.get("path") or "."
    glob = arguments.get("glob")
    if not isinstance(raw, str) or (glob is not None and not isinstance(glob, str)):
        raise ToolInputError("path and glob must be strings")
    target = os.path.realpath(os.path.join(base, os.path.expanduser(raw)))
    if not os.path.exists(target):
        raise ToolInputError(f"path not found: {target}")
    filters = ["--ignore-case"] if arguments.get("ignore_case") is True else []
    if glob:
        filters += ["--glob", glob]
    return {"pattern": pattern, "task": task.strip(), "target": target, "filters": filters + ["-e", pattern]}


def run(arguments, base, cancel_event=None, env=None, started=None):
    """(reply text, log event) for one call. Never raises for a Jev or config failure."""
    started = time.monotonic() if started is None else started
    request = parse_arguments(arguments, base)
    target = request["target"]
    root = repository_root_of(target, os.path.realpath(base))
    event = {"root": root, "pattern": request["pattern"][:200]}
    found, error, complete = rg_matches(request["filters"], [target], cwd=root,
                                        timeout_s=max(0.2, BUDGET_S - MIN_JEV_S - elapsed(started)))
    event.update({"rg_s": elapsed(started), "matched_files": len(found), "rg_complete": complete})
    head = f"root: {root}"
    if error:
        return f"{head}\nrg error: {error}", {**event, "outcome": "rg_error"}
    matches = {}
    for absolute, lines in found.items():
        relative = os.path.relpath(os.path.realpath(absolute), root).replace(os.sep, "/")
        matches[relative] = lines
    incomplete = "" if complete else " rg stopped at the time limit: the list may be incomplete."
    where = os.path.relpath(target, root)
    scope = "" if where == "." else f" under {where}"
    if not matches:
        return f"{head}\nNo matches for {request['pattern']!r}{scope}.{incomplete}", {**event, "outcome": "none"}
    by_count = sorted(matches, key=lambda p: (-len(matches[p]), p))
    total_lines = sum(len(lines) for lines in matches.values())
    summary = f"{len(matches)} file{'s' if len(matches) > 1 else ''} match{'es' if len(matches) == 1 else ''} {request['pattern']!r}{scope}.{incomplete}"
    if len(matches) <= CODE_FILES and total_lines <= SMALL_LINES:
        body = "\n".join(f"{path}:{number}:{_clip(text)}" for path in sorted(matches)
                         for number, text in sorted(matches[path].items()))
        return f"{head}\n{summary}\n\n{body}", {**event, "outcome": "small"}
    if len(matches) > MAX_FILES:
        listed = "\n".join(f"{path}: {len(matches[path])} lines" for path in by_count[:20])
        more = len(matches) - 20
        return (f"{head}\n{summary} Too broad to rank: narrow the pattern, path or glob. "
                f"Files with the most matches:\n{listed}\n... {more} more"), {**event, "outcome": "too_broad"}

    scores, reason = _score(request, root, matches, started, cancel_event, env, event)
    threshold = event.get("threshold", 0.5)
    return _render(head, summary, root, matches, by_count, scores, reason, threshold), \
        {**event, "outcome": "ranked" if scores and not reason else "partly_ranked" if scores else "unranked",
         "reason": reason, "scored_files": len(scores), "elapsed_s": elapsed(started)}


def _score(request, root, matches, started, cancel_event, env, event):
    """({path: (score, start, end)} for the files Jev scored, reason for any file left unscored)."""
    from . import engine as engine_module
    from .config import ConfigurationError, load_defaults_for
    from .prepare import find_credential_pattern

    query = build_query([request["task"]], request["pattern"])
    if find_credential_pattern(query):
        return {}, "the task or pattern looks like a credential, so nothing was sent to Jev"
    remaining = BUDGET_S - elapsed(started)
    if remaining < MIN_JEV_S:
        return {}, "no time left after rg"
    try:
        loaded = load_defaults_for(root, env=env)
    except ConfigurationError as cause:
        return {}, f"no Jev configuration ({cause.code})"
    config = loaded["config"]
    config["search"]["deadline_ms"] = int(remaining * 1000)
    config["search"]["concurrency"] = CONCURRENCY
    config["scan_caps"]["transmitted_bytes"] = MAX_BYTES  # refuses before dispatch, never truncates
    lines = {path: set(found) for path, found in matches.items()}
    result = engine_module.SearchEngine(loaded, env=dict(env if env is not None else os.environ)).search(
        {"query": query, "scope": cover(lines), "max_context_tokens": config["search"]["max_response_tokens"],
         "allow_partial_scan": True},
        {"fragment_map": snippet_mapper(lines, SNIPPET_LINES), "cancel_event": cancel_event})
    outcome = result["outcome"]
    report = outcome.get("report") or {}
    event.update({"status": outcome.get("status"), "error": (outcome.get("error") or {}).get("code"),
                  "stop_reasons": report.get("stop_reasons"),
                  "sent_bytes": (report.get("usage") or {}).get("transmitted_bytes"),
                  "jev_s": round(elapsed(started) - event["rg_s"], 2)})
    event["threshold"] = config["search"]["threshold"]
    scores = {}
    for path, start, end, score in result.get("fragment_scores") or ():
        if path in matches and score > scores.get(path, (-1.0,))[0]:
            scores[path] = (score, start, end)
    unscored = len(matches) - len(scores)
    if not unscored:
        return scores, None
    if outcome.get("status") == "error":
        return scores, f"Jev failed ({event['error']})"
    stops = [str(reason).lower().replace("_", " ") for reason in report.get("stop_reasons") or ()]
    return scores, f"{unscored} files not scored" + (f": {', '.join(stops)}" if stops else "")


def _render(head, summary, root, matches, by_count, scores, reason, threshold):
    """Code for the best files Jev scored relevant (unranked: the files with the most matches), then
    every other matching file, best first. A file scored below the threshold gets no code but stays listed."""
    ranked = sorted(scores, key=lambda p: -scores[p][0])
    rest = [path for path in by_count if path not in scores]
    if not scores:
        title = f"Unranked ({reason}); order is match count."
        code_paths = rest[:CODE_FILES]
    else:
        title = "Ranked for your task, best first" + (f"; {reason}, listed last by match count." if reason else ".")
        code_paths = [path for path in ranked if scores[path][0] >= threshold][:CODE_FILES]
        if not code_paths:
            title += " None looked clearly relevant, so no code is shown."
    blocks = [_code_block(root, path, matches[path], scores.get(path)) for path in code_paths]
    others = [path for path in ranked + rest if path not in code_paths]
    listed = "\n".join(f"{path}: {_line_list(matches[path])}" for path in others)
    parts = [head, f"{summary} {title}", *blocks]
    if others:
        parts.append("Other matching files (file: match lines):\n" + listed)
    return "\n\n".join(part for part in parts if part)


def _code_block(root, path, lines, scored):
    numbers = sorted(lines)
    if scored:
        start, end = scored[1], scored[2]
        inside = [n for n in numbers if start <= n <= end] or numbers
    else:
        start, end, inside = numbers[0], numbers[-1], numbers
    first = max(start, inside[0] - 8)
    last = min(end, first + CODE_LINES - 1)
    if last < inside[0]:  # the span starts far above its first match: centre on the match instead
        first, last = max(1, inside[0] - 8), inside[0] + CODE_LINES - 9
    try:
        with open(os.path.join(root, path), "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read().splitlines()
    except OSError:
        return f"{path}: {_line_list(lines)} (unreadable now)"
    last = min(last, len(source))
    width = len(str(last))
    code = "\n".join(f"{n:>{width}}  {_clip(source[n - 1])}" for n in range(first, last + 1))
    return f"{path}:{first}-{last} (matches: {_line_list(lines)})\n```\n{code}\n```"


def _line_list(lines):
    numbers = sorted(lines)
    shown = ", ".join(map(str, numbers[:LIST_LINES]))
    return shown + (f" +{len(numbers) - LIST_LINES} more" if len(numbers) > LIST_LINES else "")


def _clip(text):
    text = text.rstrip("\n")
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS] + "..."
