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


def snippet_mapper(lines, radius, per_file=SNIPPETS_PER_FILE):
    """Trim each candidate fragment to the span from `radius` lines before its first match to
    `radius` after its last, and drop fragments with no match, the same matches as an
    overlapping neighbour, or beyond the first `per_file` of their file (0: no cap). Keeping the
    most-matched snippets instead of the first ones ranked worse on the bench. Jev reads less
    unrelated text (its docs: filter first) and less is sent. The excerpt header names the matched lines, which ranked a little
    better on labelled replays."""
    from .tokens import count_reference_tokens
    seen, kept = set(), defaultdict(int)

    def trim(fragment):
        found = sorted(n for n in lines.get(fragment.path, ()) if fragment.start_line <= n <= fragment.end_line)
        if not found or (fragment.path, tuple(found)) in seen:
            return None
        if per_file and kept[fragment.path] >= per_file:
            return None
        seen.add((fragment.path, tuple(found)))
        kept[fragment.path] += 1
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


def _rg(filters, paths, counts, parser, cwd, timeout_s, max_bytes):
    options = (["--count", "--with-filename"] if counts else
               ["--line-number", "--with-filename", "--no-heading", "--color", "never",
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
    records = parser(output, cwd)
    return records, _rg_error(code, stderr, records, complete), complete


def _rg_error(code, stderr, records, complete):
    if code == 2 and not records and complete:
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        return "\n".join(lines[:6]) or "rg failed"


def _count_records(output, cwd):
    records = {}
    position = 0
    while position < len(output):
        nul = output.find(b"\0", position)
        end = output.find(b"\n", nul + 1)
        if nul < 0 or end < 0:
            break
        try:
            name = output[position:nul].decode("utf-8")
            records[os.path.abspath(os.path.join(cwd or "", name))] = int(output[nul + 1:end].strip())
        except (UnicodeDecodeError, ValueError):
            pass
        position = end + 1
    return records


def _line_records(output, cwd):
    records = defaultdict(dict)
    position = 0
    while position < len(output):
        nul = output.find(b"\0", position)
        end = output.find(b"\n", nul + 1)
        if nul < 0 or end < 0:
            break
        try:
            name = output[position:nul].decode("utf-8")
            path = os.path.abspath(os.path.join(cwd or "", name))
            number, separator, text = output[nul + 1:end].partition(b":")
            if separator:
                records[path][int(number)] = text.rstrip(b"\r").decode("utf-8", "replace")
        except (UnicodeDecodeError, ValueError):
            pass
        position = end + 1
    return dict(records)


def rg_matches(filters, paths, cwd=None, timeout_s=15.0, max_bytes=RG_MAX_BYTES):
    """Return absolute paths to matched line numbers and text, an error, and completeness."""
    return _rg(filters, paths, False, _line_records, cwd, timeout_s, max_bytes)


def rg_file_counts(filters, paths, cwd=None, timeout_s=15.0, max_bytes=RG_MAX_BYTES):
    """Return {path: line_count}, rg error, and whether the complete count output arrived."""
    return _rg(filters, paths, True, _count_records, cwd, timeout_s, max_bytes)


def elapsed(started):
    return round(time.monotonic() - started, 2)
