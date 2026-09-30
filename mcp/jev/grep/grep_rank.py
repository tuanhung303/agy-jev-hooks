"""Grep-then-rank helpers shared by the Claude grep hint hook and the jev_grep MCP tool.

rg finds the matching lines; these helpers turn them into what Jev scores: a query in the tagged
layout the prompt bench measured, scope entries within the request contract, and short snippets
around the matches, the first few per file.
"""
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import replace

MAX_SCOPE_ENTRIES, MAX_SCOPE_BYTES, MAX_QUERY_BYTES = 32, 4096, 8192  # request contract limits
RG_MAX_BYTES = 8 << 20


def build_query(prompts, pattern, purpose=""):
    """Tagged sections, oldest request first. Blank-line joins let a prompt's own paragraphs pass
    for request boundaries; on labelled replays the tags ranked better (AUC 0.87 vs 0.82-0.84),
    and newest-first order ranked worse."""
    requests = "\n".join(f'<request n="{n}">{text}</request>' for n, text in enumerate(prompts, 1))
    parts = [f'<recent_user_requests order="oldest first">\n{requests}\n</recent_user_requests>' if prompts else "",
             f"<search_purpose>{purpose}</search_purpose>" if purpose else "",
             f"<grep_pattern>{pattern}</grep_pattern>",
             "<question>Which code matching the grep pattern is relevant to this task?</question>"]
    query = "\n".join(part for part in parts if part)
    # The contract bounds UTF-8 bytes, not characters (Vietnamese prompts are multi-byte).
    return query.encode("utf-8")[:MAX_QUERY_BYTES].decode("utf-8", "ignore")


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


SNIPPETS_PER_FILE = 2  # the file's best snippet decides its rank; the prompt bench measured this cap


def snippet_mapper(lines, radius, per_file=SNIPPETS_PER_FILE, include_last=False):
    """Trim around matches, drop duplicate or empty fragments, and cap each file.

    Optionally retain one fragment covering its last match; Jev reads less unrelated text.
    """
    from .tokens import count_reference_tokens
    seen, kept, kept_last = set(), defaultdict(int), set()
    last = {path: max(found) for path, found in lines.items() if found} if include_last else {}

    def trim(fragment):
        found = sorted(n for n in lines.get(fragment.path, ()) if fragment.start_line <= n <= fragment.end_line)
        if not found or (fragment.path, tuple(found)) in seen:
            return None
        is_last = fragment.start_line <= last.get(fragment.path, -1) <= fragment.end_line
        if per_file and kept[fragment.path] >= per_file:
            if not include_last or not is_last or fragment.path in kept_last:
                return None
        seen.add((fragment.path, tuple(found)))
        kept[fragment.path] += 1
        if is_last:
            kept_last.add(fragment.path)
        start = max(fragment.start_line, found[0] - radius)
        end = min(fragment.end_line, found[-1] + radius)
        rows = re.findall(r"[^\n]*\n|[^\n]+$", fragment.text)
        skip = start - fragment.start_line
        text = "".join(rows[skip:end - fragment.start_line + 1])
        byte_start = fragment.byte_start + len("".join(rows[:skip]).encode("utf-8"))
        size = len(text.encode("utf-8"))
        note = "grep matches on lines " + ", ".join(map(str, found[:12]))
        return replace(fragment, start_line=start, end_line=end, text=text, byte_start=byte_start,
                       byte_end=byte_start + size, byte_count=size, token_count=count_reference_tokens(text),
                       label=f"{fragment.label}; {note}" if fragment.label else note)
    return trim


def _rg(filters, paths, counts, cwd, timeout_s, max_bytes):
    options = ["--color", "never"] + (["--count", "--with-filename"] if counts else
                                        ["--line-number", "--with-filename", "--no-heading",
                                         "--max-columns", "300", "--max-columns-preview"])
    command = ["rg", *options, "--null", *filters, "--", *paths]
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except OSError as cause:
        return {}, f"rg could not start: {cause.strerror}", False
    stopped = threading.Event()

    def stop():
        stopped.set()
        process.kill()
    timer = threading.Timer(max(0.0, timeout_s), stop)
    timer.start()
    output, read = bytearray(), 0
    try:
        for raw in process.stdout:
            read += len(raw)
            if read > max_bytes:
                stop()
                break
            output.extend(raw)
        process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", "replace")
        code = process.wait()
    finally:
        timer.cancel()
    complete = not stopped.is_set()
    records = _records(output, cwd, counts)
    error = ("rg could not read some files." if records else stderr.strip() or "rg failed") if code == 2 else None
    return records, error, complete


def _records(output, cwd, counts):
    records = {} if counts else defaultdict(dict)
    for match in re.finditer(rb"(.*?)\0([0-9]+)(?::([^\n]*))?\n", output, re.S):
        try:
            path = os.path.abspath(os.path.join(cwd or "", match[1].decode("utf-8")))
            number = int(match[2])
        except (UnicodeDecodeError, ValueError):
            continue
        if counts:
            records[path] = number
        elif match[3] is not None:
            records[path][number] = match[3].rstrip(b"\r").decode("utf-8", "replace")
    return records if counts else dict(records)


def rg_matches(filters, paths, cwd=None, timeout_s=15.0, max_bytes=RG_MAX_BYTES):
    """Return absolute paths to matched line numbers and text, an error, and completeness."""
    return _rg(filters, paths, False, cwd, timeout_s, max_bytes)


def rg_file_counts(filters, paths, cwd=None, timeout_s=15.0, max_bytes=RG_MAX_BYTES):
    """Return {path: line_count}, rg error, and whether the complete count output arrived."""
    return _rg(filters, paths, True, cwd, timeout_s, max_bytes)


def elapsed(started):
    return round(time.monotonic() - started, 2)
