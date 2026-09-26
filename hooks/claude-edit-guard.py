#!/usr/bin/env python3
"""Shadow-first PostToolUse edit signal detector for Claude Code."""
import hashlib
from bisect import bisect_right
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
STATE_DIR = Path(os.environ.get("CLAUDE_EDIT_GUARD_STATE") or
                 Path.home() / ".local/state/agy-jev-hooks/claude-edit-guard")
SENTINEL_PATH = Path.home() / ".config/agy/sage.sentinel"
TOTAL_BUDGET_S = 10.0
MAX_CALLS_PER_CATEGORY = 10
CONTEXT_CHAR_CAP = 6000
MAX_NUDGES = 5
ACTIONS = {
    "tamper": "Fix the code, not the test, or say why the test was wrong.",
    "shallow": "Consider adding an assertion that checks the behavior.",
    "slop": "Consider replacing the placeholder with a complete implementation.",
}


def _ensure_sage_path():
    for candidate in (REPO_DIR, Path.home() / ".config/agy"):
        if (candidate / "sage/jev/request/parser.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return


_ensure_sage_path()
try:
    from sage.jev.evidence.redact import _RedactBudget, _redact_field
    from sage.jev.request.parser import build_request, load_case, parse_boolean_answers
    from sage.jev.transport import _call_jev
    THRESHOLDS = dict(load_case("edit_guard")["parse"].get("thresholds") or {})
except Exception:
    _RedactBudget = _redact_field = build_request = load_case = parse_boolean_answers = _call_jev = None
    THRESHOLDS = {"q_tamper": 0.8, "q_shallow": 0.8, "q_slop": 0.8}

TEST_SUFFIX = re.compile(r"(?:^|/)(?:tests?/|__snapshots__/)|(?:^|/)test_[^/]*\.py$|(?:^|/)[^/]*_test\.py$|_test\.go$|\.(?:test|spec)\.[cm]?[jt]sx?$|\.snap$", re.I)
CODE_SUFFIX = re.compile(r"\.(?:py|pyi|go|rs|js|jsx|mjs|cjs|ts|tsx|java|kt|rb|php|cs|cpp|c|h|swift)$", re.I)
ASSERT = re.compile(r"\bassert\b|\b(?:expect|assertThat|require)\s*\(")
WEAK = re.compile(r"\bis\s+not\s+None\b|\bisinstance\s*\(|\bassert\s+True\b|\blen\s*\([^)]*\)\s*>\s*0|\bassert_called(?:_once)?\s*\(|\.toBe(?:Defined|Truthy)\s*\(", re.I)
TAMPER = re.compile(r"\b(?:skip|skipif|xfail)\b|\.only\s*\(|\b(?:pytest\.mark\.skip|unittest\.skip)\b|toMatchSnapshot|toMatchInlineSnapshot", re.I)
TEST_INVOCATION = re.compile(r"\b(?:pytest\.main|unittest\.main|test_[a-zA-Z0-9_]+)\s*\(", re.I)
SLOP = re.compile(r"\b(?:TODO|FIXME|XXX|placeholder|not implemented|implement later)\b|NotImplementedError", re.I)
SWALLOW = re.compile(r"except\b[^:]*:\s*(?:pass\b|continue\b|return\s+(?:None|\{\}|\[\])\b)|catch\s*\{\s*\}", re.I | re.S)
STUB_RETURN = re.compile(r"^\s*return\s+(?:None|\{\}|\[\])\s*(?:#.*)?$", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(value, label, budget=None):
    if _redact_field is None:
        raise RuntimeError("redactor unavailable")
    result = _redact_field(value, label, budget)
    return result


def _path_kind(path):
    normalized = str(path).replace("\\", "/")
    if TEST_SUFFIX.search(normalized):
        return "test"
    if CODE_SUFFIX.search(normalized):
        return "source"
    return None


def _hunks(payload):
    tool = str(payload.get("tool_name") or "")
    response = payload.get("tool_response") or {}
    inputs = payload.get("tool_input") or {}
    if response.get("userModified"):
        return []
    patches = response.get("structuredPatch")
    if isinstance(patches, list) and patches:
        out = []
        for patch in patches:
            lines = patch.get("lines") if isinstance(patch, dict) else None
            if not isinstance(lines, list):
                continue
            old_no = int(patch.get("oldStart") or 1)
            new_no = int(patch.get("newStart") or 1)
            records = []
            for line in lines:
                if not isinstance(line, str) or line.startswith("\\"):
                    continue
                marker, text = (line[:1], line[1:]) if line else (" ", "")
                if marker == "-":
                    records.append(("old", new_no, text))
                    old_no += 1
                elif marker == "+":
                    records.append(("new", new_no, text))
                    new_no += 1
                else:
                    records.append(("ctx", new_no, text))
                    old_no += 1
                    new_no += 1
            out.append(records)
        return out
    if tool == "Write" and response.get("type") == "create" and response.get("originalFile") is None:
        content = inputs.get("content", response.get("content"))
        if isinstance(content, str):
            return [[("new", i, line) for i, line in enumerate(content.splitlines(), 1)]]
    if tool == "MultiEdit":
        edits = inputs.get("edits")
        if not isinstance(edits, list):
            return []
        made = []
        for edit in edits:
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                continue
            made.append([("old", i, x) for i, x in enumerate(old.splitlines(), 1)] +
                        [("new", i, x) for i, x in enumerate(new.splitlines(), 1)])
        return made
    return []


def _signals(kind, hunks, path=""):
    signals = {"tamper": set(), "shallow": set(), "slop": set()}
    for hunk in hunks:
        olds = [(n, t) for side, n, t in hunk if side == "old"]
        news = [(n, t) for side, n, t in hunk if side == "new"]
        added = "\n".join(t for _, t in news)
        removed = "\n".join(t for _, t in olds)
        if kind == "test":
            if any(ASSERT.search(t) for _, t in olds) and not any(ASSERT.search(t) for _, t in news):
                signals["tamper"].update(n for n, t in olds if ASSERT.search(t))
            for n, line in news:
                if TAMPER.search(line):
                    signals["tamper"].add(n)
            if WEAK.search(added):
                signals["shallow"].update(n for n, t in news if WEAK.search(t) or ASSERT.search(t))
            if re.search(r"\bdef\s+test_\w+|\bit\s*\(\s*['\"]|\btest\s*\(\s*['\"]", added) and not ASSERT.search(added):
                signals["shallow"].update(n for n, t in news if re.search(r"\bdef\s+test_|\bit\s*\(|\btest\s*\(", t))
            if removed and re.search(r"\bdef\s+test_\w+|\bit\s*\(\s*['\"]|\btest\s*\(\s*['\"]", removed):
                signals["tamper"].update(n for n, t in olds if re.search(r"\bdef\s+test_|\bit\s*\(|\btest\s*\(", t))
            if removed and TEST_INVOCATION.search(removed):
                signals["tamper"].update(n for n, t in olds if TEST_INVOCATION.search(t))
            if olds and news and any(ASSERT.search(t) for _, t in olds) and any(ASSERT.search(t) for _, t in news):
                signals["tamper"].update(n for n, _ in news)
            if str(path).lower().endswith(".snap"):
                signals["tamper"].update(n for n, _ in olds + news)
        if kind == "source":
            added_lines = []
            line_starts = []
            offset = 0
            for number, line in news:
                line_starts.append(offset)
                added_lines.append(line)
                offset += len(line) + 1
            added_text = "\n".join(added_lines)
            for number, line in news:
                if SLOP.search(line) or STUB_RETURN.search(line):
                    signals["slop"].add(number)
            for match in SWALLOW.finditer(added_text):
                start_line = bisect_right(line_starts, match.start()) - 1
                end_line = bisect_right(line_starts, max(match.start(), match.end() - 1)) - 1
                signals["slop"].update(news[index][0] for index in range(start_line, end_line + 1))
    return {k: sorted(v) for k, v in signals.items() if v}


def _bounded_context(hunks, signals):
    """Render merged six-line neighborhoods around signal lines."""
    windows = []
    for hunk_index, hunk in enumerate(hunks):
        indices_by_line = {}
        for i, (side, number, _) in enumerate(hunk):
            if side in ("old", "new"):
                indices_by_line.setdefault(number, []).append(i)
        for category, line_numbers in signals.items():
            for line_number in line_numbers:
                for index in indices_by_line.get(line_number, ()):
                    start, end = max(0, index - 3), min(len(hunk), index + 4)
                    windows.append([hunk_index, start, end, {category}])
    windows.sort(key=lambda item: (item[0], item[1], item[2]))
    merged = []
    for hunk_index, start, end, categories in windows:
        if merged and hunk_index == merged[-1][0] and start <= merged[-1][2]:
            merged[-1][2] = max(merged[-1][2], end)
            merged[-1][3].update(categories)
        else:
            merged.append([hunk_index, start, end, set(categories)])

    def render(window):
        hunk_index, start, end, _ = window
        return [f"{side}:{number}: {text}" for side, number, text in hunks[hunk_index][start:end]]

    rendered = [line for window in merged for line in render(window)]
    while len("\n".join(rendered)) > CONTEXT_CHAR_CAP:
        removable = None
        for i in range(len(merged) - 1, -1, -1):
            candidate_categories = merged[i][3]
            if all(any(j < i and category in merged[j][3] for j in range(len(merged)))
                   for category in candidate_categories):
                removable = i
                break
        if removable is None:
            break
        del merged[removable]
        rendered = [line for window in merged for line in render(window)]
    # Cut on a line boundary: a sliced secret can survive redaction.
    text = "\n".join(rendered)
    if len(text) > CONTEXT_CHAR_CAP:
        text = text[:text.rfind("\n", 0, CONTEXT_CHAR_CAP + 1)] if "\n" in text[:CONTEXT_CHAR_CAP] else ""
    return text


def _counter(session, name):
    return STATE_DIR / f"{hashlib.sha256(session.encode()).hexdigest()[:24]}.{name}.count"


def _count(session, name):
    try:
        return int(_counter(session, name).read_text().strip())
    except (OSError, ValueError):
        return 0


def _bump(session, name):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _counter(session, name).write_text(str(_count(session, name) + 1))


def _seen(session, digest):
    return STATE_DIR / f"{hashlib.sha256(session.encode()).hexdigest()[:24]}.{digest}.seen"


def _log(record):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def analyze(payload):
    """Return candidate record and safe nudge lines; any unsafe evidence abstains."""
    started = time.monotonic()
    session = str(payload.get("session_id") or "")
    tool_id = str(payload.get("tool_use_id") or "")
    transcript = str(payload.get("transcript_path") or "")
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    inputs, response = payload.get("tool_input") or {}, payload.get("tool_response") or {}
    file_path = response.get("filePath") or inputs.get("file_path") or inputs.get("filePath") or ""
    kind = _path_kind(file_path)
    if not kind or response.get("userModified"):
        return None, []
    hunks = _hunks(payload)
    if not hunks or not any(side in ("new", "old") for h in hunks for side, _, _ in h):
        return None, []
    signals = _signals(kind, hunks, file_path)
    if not signals:
        return None, []
    all_text = []
    for hunk in hunks:
        all_text.extend(f"{side}:{number}: {text}" for side, number, text in hunk)
    raw_hunk = "\n".join(all_text)
    digest = hashlib.sha256((str(file_path) + "\n" + raw_hunk).encode()).hexdigest()
    if _seen(session, digest).exists():
        return None, []
    safe_budget = _RedactBudget(5.0)
    try:
        safe_path = _safe(str(file_path), "file path", safe_budget)
        safe_hunk = _safe(_bounded_context(hunks, signals), "hunk", safe_budget)
        safe_signals = json.dumps(signals, sort_keys=True)
        safe_signals = _safe(safe_signals, "signals", safe_budget)
    except Exception:
        return None, []
    # The marker text from an omitted field is a refusal signal, never evidence.
    # An empty context (one line longer than the cap) gives Jev nothing to judge.
    redaction_omitted = not safe_hunk.strip() or "omitted:" in safe_hunk or "omitted:" in safe_path
    if "omitted:" in safe_path:
        return None, []
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _seen(session, digest).touch()
    except OSError:
        pass
    questions_by_category = {"tamper": "q_tamper", "shallow": "q_shallow", "slop": "q_slop"}
    active_categories = [category for category in signals
                         if _count(session, f"calls-{category}") < MAX_CALLS_PER_CATEGORY]
    qkeys = [questions_by_category[category] for category in active_categories]
    scores, error = {}, None
    if redaction_omitted:
        error = "redaction omitted"
    elif not active_categories:
        error = "call cap"
    elif _call_jev is None:
        error = "jev unavailable"
    else:
        try:
            body = build_request("edit_guard", {"file": safe_path, "hunk": safe_hunk, "signals": safe_signals})
            body["questions"] = {k: v for k, v in body["questions"].items() if k in qkeys}
            remaining = TOTAL_BUDGET_S - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("deadline exhausted")
            for category in active_categories:
                _bump(session, f"calls-{category}")
            data = _call_jev(body["state"], body["questions"], min(8.0, remaining), time.monotonic() + remaining)
            scores = parse_boolean_answers(data, qkeys)
            if set(scores) != set(qkeys):
                raise ValueError("incomplete Jev answers")
        except Exception as exc:
            error = type(exc).__name__
            scores = {}
    fired = []
    for category, qkey in questions_by_category.items():
        if category in signals and scores.get(qkey, 0.0) >= THRESHOLDS[qkey]:
            fired.append(category)
    decision = "would_nudge" if fired else ("error" if error else "pass")
    try:
        safe_file = _safe(_display_file(file_path, cwd), "file path", safe_budget)
        safe_transcript = _safe(transcript, "transcript path", safe_budget)
        safe_cwd = _safe(str(cwd), "cwd", safe_budget)
    except Exception:
        return None, []
    record = {
        "ts": _now(), "session": session, "tool_use_id": tool_id,
        "transcript_path": safe_transcript, "cwd": safe_cwd,
        "file": safe_file,
        "tool": str(payload.get("tool_name") or ""), "kind": kind,
        "signals": signals, "scores": scores, "decision": decision,
        "latency_s": round(time.monotonic() - started, 3), "error": error,
    }
    candidates = []
    for category in fired:
        try:
            safe_file = _safe(_display_file(file_path, cwd), "file path", safe_budget)
        except Exception:
            return None, []
        candidates.append((category, signals[category][0], safe_file, scores[questions_by_category[category]]))
    return record, candidates


def _inside(path, cwd):
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        candidate.resolve().relative_to(cwd)
        return True
    except (OSError, ValueError):
        return False


def _display_file(path, cwd):
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return str(candidate.resolve().relative_to(cwd))
    except (OSError, ValueError):
        return str(path)


def main():
    if os.environ.get("CLAUDE_EDIT_GUARD") == "0" or SENTINEL_PATH.exists():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    record, candidates = analyze(payload)
    if not record:
        return 0
    mode = "nudge" if os.environ.get("CLAUDE_EDIT_GUARD_MODE") == "nudge" else "shadow"
    nudges = []
    if mode == "nudge":
        session = record["session"]
        if _count(session, "nudges") < MAX_NUDGES:
            for category, line, path, score in candidates:
                key = f"nudge-{hashlib.sha256((path + category).encode()).hexdigest()[:24]}"
                if _count(session, key):
                    continue
                budget = _RedactBudget(1.0)
                try:
                    what = {
                        "tamper": "weakens or bypasses a test",
                        "shallow": "adds a test that checks little behavior",
                        "slop": "adds a swallowed exception or placeholder",
                    }[category]
                    text = _safe(f"[edit-guard] {path}:{line} {what} (Jev {score:.2f}). {ACTIONS[category]}",
                                 "stderr", budget)
                except Exception:
                    continue
                text = text[:200]
                _bump(session, key)
                _bump(session, "nudges")
                nudges.append(text)
                if _count(session, "nudges") >= MAX_NUDGES:
                    break
    record["decision"] = "nudge" if nudges else record["decision"]
    _log(record)
    if nudges:
        sys.stderr.write("\n".join(nudges) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    # except Exception, not BaseException: SystemExit(2) must reach Claude.
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
