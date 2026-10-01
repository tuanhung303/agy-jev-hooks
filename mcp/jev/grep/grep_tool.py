"""Search with rg, then rank bounded match sets with Jev. Only listed paths and ranked snippets appear."""
import os
import subprocess
import time
from collections import Counter
from itertools import islice

from .grep_rank import RG_MAX_BYTES, build_query, cover, elapsed, rg_file_counts, rg_matches, snippet_mapper

CODE_FILES, CODE_LINES = 3, 20
SMALL_LINES, MAX_FILES = 40, 60
MAX_BYTES, BUDGET_S, MIN_JEV_S = 600_000, 3.0, 0.5
SNIPPET_LINES, CONCURRENCY = 12, 32
LIST_LINES, TEXT_CHARS = 6, 240

TOOL_NAME = "jev_grep"
TOOL_DESCRIPTION = (
    "rg regex search, matched files ranked by Jev for `task`. Returns code from best few files, lists other "
    "matches. Over 60 matching files: folder summary only; narrow with `path` or `glob`. Use for broad search by "
    "behavior. Known symbol or one file: plain rg."
)
TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "rg regex."},
        "task": {"type": "string", "description": "One sentence: what you seek and why. Drives ranking."},
        "path": {"type": "string", "description": "File or folder, absolute or relative to base folder. Default: base folder."},
        "glob": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                 "description": "rg --glob filters, applied in order."},
        "ignore_case": {"type": "boolean", "description": "Case-insensitive."},
        "no_ignore": {"type": "boolean", "description": "Also search gitignored files. Nested repo a parent ignores: pass its `path` instead."},
    },
    "required": ["pattern", "task"],
    "additionalProperties": False,
}


class ToolInputError(ValueError):
    pass


def repository_root_of(path, base):
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        top = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=2, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    if top:
        return os.path.realpath(top)
    return base if _inside(path, base) else folder


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def parse_arguments(arguments, base):
    if not isinstance(arguments, dict):
        raise ToolInputError("arguments must be an object")
    pattern, task = arguments.get("pattern"), arguments.get("task")
    if not isinstance(pattern, str) or not pattern:
        raise ToolInputError("pattern is required")
    if not isinstance(task, str) or not task.strip():
        raise ToolInputError("task is required: one sentence on what you are looking for")
    raw, globs = arguments.get("path") or ".", arguments.get("glob")
    if isinstance(globs, str):
        globs = [globs]
    if globs is None:
        globs = []
    if not isinstance(raw, str) or not isinstance(globs, list) or any(not isinstance(g, str) for g in globs):
        raise ToolInputError("path must be a string and glob must be a string or list of strings")
    target = os.path.realpath(os.path.join(base, os.path.expanduser(raw)))
    if not os.path.exists(target):
        raise ToolInputError(f"path not found: {target}")
    filters = ["--no-config", "--text"]
    if arguments.get("ignore_case") is True:
        filters.append("--ignore-case")
    if arguments.get("no_ignore") is True:
        filters.append("--no-ignore")
    for glob in globs:
        filters.extend(("--glob", glob))
    filters.extend(("-e", pattern))
    return {"pattern": pattern, "task": task.strip(), "target": target, "filters": filters,
            "no_ignore": arguments.get("no_ignore") is True}


def run(arguments, base, cancel_event=None, env=None, started=None, show_root=True):
    started = time.monotonic() if started is None else started
    request = parse_arguments(arguments, os.path.realpath(base))
    target, base = request["target"], os.path.realpath(base)
    git_root = repository_root_of(target, base)
    display_root = base if _inside(target, base) else (target if os.path.isdir(target) else os.path.dirname(target))
    head = f"root: {display_root}" if show_root or display_root != base else ""
    event = {"root": git_root, "pattern": request["pattern"][:200]}
    rel_target = os.path.relpath(target, git_root).replace(os.sep, "/")
    phase_a_budget = max(0.0, BUDGET_S - MIN_JEV_S - elapsed(started))
    counts, error, complete = rg_file_counts(request["filters"], [rel_target], cwd=git_root,
                                              timeout_s=phase_a_budget, max_bytes=RG_MAX_BYTES)
    event.update({"rg_s": elapsed(started), "matched_files": len(counts), "rg_complete": complete})
    if error and not counts:
        return _finish(_join(head, f"rg error: {error}"), event, started, "rg_error")
    warning = error if counts else None
    event.update({"rg_warning": warning} if warning else {})
    if not complete or len(counts) > MAX_FILES:
        text = _too_broad(head, target, display_root, counts, complete, warning=warning)
        return _finish(text, event, started, "too_broad")
    if not counts:
        return _finish(_join(head, "No matches.", warning=warning), event, started, "none")

    paths = sorted(os.path.relpath(path, git_root).replace(os.sep, "/") for path in counts)
    found, error, complete = rg_matches(request["filters"], paths, cwd=git_root,
                                         timeout_s=max(0.0, BUDGET_S - MIN_JEV_S - elapsed(started)),
                                         max_bytes=RG_MAX_BYTES)
    event.update({"rg_s": elapsed(started), "rg_complete": complete})
    missing = {os.path.join(git_root, path) for path in paths} - found.keys()
    found.update({path: {} for path in missing if _file_status(path) == "unreadable"})
    if error and not found:
        return _finish(_join(head, f"rg error: {error}"), event, started, "rg_error")
    if error:
        event["rg_warning"] = warning = error
    if not complete:
        return _finish(_too_broad(head, target, display_root, counts, True,
                                   "Match details exceeded the output or time limit.", warning),
                       event, started, "too_broad")
    matches = {os.path.relpath(os.path.realpath(path), git_root).replace(os.sep, "/"): lines
               for path, lines in found.items()}
    event["matched_files"] = len(matches)
    if not matches:
        return _finish(_join(head, "No matches.", warning=warning), event, started, "none")
    statuses = {path: _file_status(os.path.join(git_root, path)) for path in matches}
    binary_paths, unreadable_paths = ({p for p, s in statuses.items() if s == k} for k in ("binary", "unreadable"))
    text_matches = {path: lines for path, lines in matches.items() if statuses[path] == "text"}
    summary = f"{len(matches)} {'file' if len(matches) == 1 else 'files'} match."
    binary_list = "\n".join(f"{_display_path(path, git_root, display_root)} (binary)" for path in sorted(binary_paths))
    unreadable_list = "\n".join(f"{_display_path(p, git_root, display_root)} (unreadable now)" for p in sorted(unreadable_paths))
    if not text_matches:
        return _finish(_join(head, summary, "Binary matches:\n" + binary_list if binary_list else "",
                             "Unreadable files:\n" + unreadable_list if unreadable_list else "", warning=warning),
                       event, started, "unreadable" if unreadable_paths and not binary_paths else "binary")
    by_count = sorted(text_matches, key=lambda path: (-len(text_matches[path]), path))
    total_lines = sum(len(lines) for lines in text_matches.values())
    if len(text_matches) <= CODE_FILES and total_lines <= SMALL_LINES:
        body = "\n".join(f"{_display_path(path, git_root, display_root)}:{number}:{_clip(text)}"
                          for path in sorted(text_matches) for number, text in sorted(text_matches[path].items()))
        body = _join(body, "Binary matches:\n" + binary_list if binary_paths else "",
                     "Unreadable files:\n" + unreadable_list if unreadable_paths else "")
        return _finish(_join(head, body, warning=warning), event, started, "small")

    scores, reason = _score(request, git_root, text_matches, started, cancel_event, env, event)
    event.update({"scored_files": len(scores)})
    rendered = _render(git_root, display_root, head, summary, text_matches, by_count, scores, reason,
                       event.get("threshold", 0.5), binary_paths, unreadable_paths, warning)
    outcome = "ranked" if scores and not reason else "partly_ranked" if scores else "unranked"
    return _finish(rendered, event, started, outcome, reason=reason)


def _finish(text, event, started, outcome, **extra):
    event.update(extra, outcome=outcome, elapsed_s=elapsed(started))
    return text, event


def _display_path(path, git_root, display_root):
    return os.path.relpath(os.path.join(git_root, path), display_root).replace(os.sep, "/")


def _too_broad(head, target, display_root, counts, complete, note="", warning=None):
    folders = Counter(
        relative.split("/", 1)[0] if "/" in relative else "(top level)"
        for path in counts for relative in [os.path.relpath(path, target if os.path.isdir(target) else os.path.dirname(target)).replace(os.sep, "/")])
    ordered = sorted(folders.items(), key=lambda item: (-item[1], item[0]))
    rows = [f"{folder + '/' if folder != '(top level)' else folder}: {count}" for folder, count in ordered[:8]]
    if len(ordered) > 8:
        rows.append(f"+{len(ordered) - 8} more folders")
    text = f"{'at least ' if not complete or warning else ''}{len(counts)} matching files; too broad to rank. Narrow with a path or glob."
    return _join(head, text + (" The scan may be incomplete." if not complete else "") +
                 (f" {note}" if note else "") + ("\n" + "\n".join(rows) if rows else ""), warning=warning)


def _score(request, root, matches, started, cancel_event, env, event):
    from . import engine as engine_module
    from .config import ConfigurationError, load_defaults_for
    from .prepare import find_credential_pattern

    query = build_query([request["task"]], request["pattern"])
    if find_credential_pattern(query):
        return {}, "not scorable (credential-like task or pattern)"
    remaining = BUDGET_S - elapsed(started)
    if remaining <= 0:
        return {}, "time limit"
    try:
        loaded = load_defaults_for(root, env=env)
    except ConfigurationError as cause:
        return {}, f"ranking service unavailable ({cause.code})"
    config = loaded["config"]
    config["search"]["deadline_ms"] = int(remaining * 1000)
    config["search"]["concurrency"] = CONCURRENCY
    config["scan_caps"]["transmitted_bytes"] = MAX_BYTES
    if request["no_ignore"]:
        config["source"]["respect_gitignore"] = False
    lines = {path: set(found) for path, found in matches.items()}
    result = engine_module.SearchEngine(loaded, env=dict(env if env is not None else os.environ)).search(
        {"query": query, "scope": cover(lines), "max_context_tokens": config["search"]["max_response_tokens"],
         "allow_partial_scan": True},
        {"fragment_map": snippet_mapper(lines, SNIPPET_LINES, include_last=True), "cancel_event": cancel_event,
         "path_filter": set(lines)})
    outcome, report = result["outcome"], (result["outcome"].get("report") or {})
    event.update({"status": outcome.get("status"), "error": (outcome.get("error") or {}).get("code"),
                  "stop_reasons": report.get("stop_reasons"),
                  "sent_bytes": (report.get("usage") or {}).get("transmitted_bytes"),
                  "jev_s": round(elapsed(started) - event["rg_s"], 2),
                  "threshold": config["search"]["threshold"]})
    scores = {}
    for path, start, end, score in result.get("fragment_scores") or ():
        if path in matches and score > scores.get(path, (-1.0,))[0]:
            scores[path] = (score, start, end)
    if len(scores) == len(matches):
        return scores, None
    stop_reasons = set(report.get("stop_reasons") or ())
    reasons = dict.fromkeys(("PROVIDER_AUTH", "PROVIDER_QUOTA", "PROVIDER_RATE_LIMIT", "PROVIDER_UNAVAILABLE",
                             "INVALID_PROVIDER_RESPONSE"), "ranking service error")
    reasons.update({"CANCELLED": "cancelled", "DEADLINE": "time limit",
                    "SCAN_CAP_REACHED": "size limit", "SCOPE_EXCEEDS_SCAN_BUDGET": "size limit"})
    if outcome.get("status") == "error":
        code = event["error"] or next((code for code in stop_reasons if reasons.get(code) == "ranking service error"),
                                      "unknown")
        return scores, f"ranking service error ({code})"
    why = next((reasons[code] for code in stop_reasons if code in reasons), None)
    if why == "ranking service error":
        return scores, f"ranking service error ({', '.join(sorted(stop_reasons))})"
    if cancel_event is not None and cancel_event.is_set():
        why = "cancelled"
    elif why is None:
        why = "time limit" if elapsed(started) >= BUDGET_S else "not scorable (too large, binary, or excluded)"
    return scores, why


def _render(root, display_root, head, summary, matches, by_count, scores, reason, threshold,
            binary_paths=(), unreadable_paths=(), warning=None):
    unreadable_paths = set(unreadable_paths)
    unreadable_paths.update(path for path in matches if _file_status(os.path.join(root, path), check_binary=False) == "unreadable")
    readable_paths = matches.keys() - unreadable_paths
    ranked = [path for path in sorted(scores, key=lambda path: -scores[path][0]) if path in readable_paths]
    rest = [path for path in by_count if path not in scores and path in readable_paths]
    if not ranked:
        title, code_paths = f"Unranked ({reason or 'no ranking'}); by match count.", rest[:CODE_FILES]
    else:
        title = "Ranked."
        total_files = len(readable_paths) + len(binary_paths) + len(unreadable_paths)
        if len(ranked) < total_files:
            title += f" Scored {len(ranked)} of {total_files}; {reason or 'some files were not scorable'}."
        code_paths = [path for path in ranked if scores[path][0] >= threshold][:CODE_FILES]
    blocks = [_code_block(root, display_root, path, matches[path], scores.get(path)) for path in code_paths]
    parts = [head, f"{summary} {title}", *blocks]
    keyword_paths = ranked[:2] if scores and not code_paths else []
    if keyword_paths:
        previews = []
        for path in keyword_paths:
            shown = sorted(matches[path].items())[:5]
            previews.extend(f"{_display_path(path, root, display_root)}:{n}:{_clip(line)}" for n, line in shown)
            if len(matches[path]) > len(shown):
                previews.append(f"{_display_path(path, root, display_root)}: +{len(matches[path]) - len(shown)} more")
        parts.append("Best keyword matches, not judged relevant:\n" + "\n".join(previews))
    unreadable_paths.update(path for path in matches if path not in unreadable_paths and _file_status(os.path.join(root, path), check_binary=False) == "unreadable")
    others = [path for path in ranked + rest if path not in code_paths and path not in keyword_paths and path not in unreadable_paths]
    if others:
        listed = "\n".join(f"{_display_path(path, root, display_root)}: {_line_list(matches[path])}"
                            for path in others)
        parts.append("Other matches:\n" + listed)
    for title, paths, label in (("Binary matches", binary_paths, "binary"), ("Unreadable files", unreadable_paths, "unreadable now")):
        if paths:
            parts.append(title + ":\n" + "\n".join(
                f"{_display_path(path, root, display_root)} ({label})" for path in sorted(paths)))
    return _join(*parts, warning=warning)


def _code_block(root, display_root, path, lines, scored):
    numbers = sorted(lines)
    start, end = (scored[1], scored[2]) if scored else (numbers[0], numbers[-1])
    inside = [number for number in numbers if start <= number <= end] or numbers
    first = max(start, inside[0] - 8)
    last = min(end, first + CODE_LINES - 1)
    if last < inside[0]:
        first, last = max(1, inside[0] - 8), inside[0] + CODE_LINES - 9
    try:
        with open(os.path.join(root, path), "r", encoding="utf-8", errors="replace", newline="\n") as handle:
            source = list(islice(handle, first - 1, last))
    except OSError:
        return f"{_display_path(path, root, display_root)}: {_line_list(lines)} (unreadable now)"
    last = first - 1 + len(source)
    width = len(str(last))
    code = "\n".join(f"{number:>{width}}  {_clip(line)}" for number, line in enumerate(source, first))
    shown = [number for number in numbers if first <= number <= last]
    also = [number for number in numbers if number not in shown][:5]
    omitted = len(numbers) - len(shown) - len(also)
    extras = ("\nalso:\n" + "\n".join(f"{n}: {_clip(lines[n], 160)}" for n in also) +
              (f"\n+{omitted} more" if omitted else "")) if also else ""
    count = f"{len(numbers)} {'match' if len(numbers) == 1 else 'matches'}"
    header = count if also else f"matches: {_line_list(lines)}"
    return f"{_display_path(path, root, display_root)}:{first}-{last} ({header})\n```\n{code}\n```{extras}"


def _line_list(lines):
    numbers = sorted(lines)
    return ", ".join(map(str, numbers[:LIST_LINES])) + (
        f" +{len(numbers) - LIST_LINES} more" if len(numbers) > LIST_LINES else "")


def _clip(text, limit=TEXT_CHARS):
    return text.rstrip("\r\n")[:limit] + ("..." if len(text.rstrip("\r\n")) > limit else "")


def _file_status(path, check_binary=True):
    try:
        with open(path, "rb") as source:
            return "text" if not check_binary or not any(b"\0" in chunk for chunk in iter(lambda: source.read(64 * 1024), b"")) else "binary"
    except OSError:
        return "unreadable"


def _join(*parts, warning=None):
    return "\n\n".join(part for part in (*parts, warning) if part)
