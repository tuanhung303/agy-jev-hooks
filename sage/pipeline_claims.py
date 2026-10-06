"""Receipt checks for pipeline and data completion claims."""
import re
from collections import Counter

DBT_CLAIM_RE = re.compile(
    r"\bdbt\s+(?:build|run|test)\b.{0,50}\b(?:pass(?:ed)?|succeed(?:ed)?|completed|clean)\b|"
    r"\bPASS\s*=\s*\d+[^\n]{0,80}\bERROR\s*=\s*0\b", re.I)
BUILD_CLAIM_RE = re.compile(
    r"\bbuild\s+(?:pass(?:ed)?|succeed(?:ed)?|completed|clean)\b", re.I)
PIPELINE_CLAIM_RE = re.compile(
    r"\b(?:pipeline|job|run)\b(?:[\s_-]+[\w.-]+){0,4}\s+"
    r"(?:succeed(?:ed)?|completed|passed|finished successfully)\b|"
    r"\b(?:pipeline|job|run)\s+(?:đã\s+)?chạy\s+xong\b", re.I)
COMPILE_CLAIM_RE = re.compile(
    r"\b(?:compiled|compile|parse)(?:\s+\w+){0,2}\s+"
    r"(?:clean|successfully|passed)\b|\bparse\s+clean\b", re.I)
DATA_CLAIM_RE = re.compile(
    r"\b(?:row|record|data)\s+counts?\s+(?:match(?:ed)?|reconciled)\b|"
    r"\b(?:row|record|data)\s+(?:are\s+)?reconciled\b|"
    r"\b(?:reconciled|reconcile[d]?)\s+(?:row|record|data)\s+counts?\b", re.I)
VIETNAMESE_RUN_CLAIM_RE = re.compile(r"\bđã\s+chạy\s+xong\b", re.I)


def _count_values(output):
    values = []
    for line in str(output or "").splitlines():
        labeled = re.findall(
            r"\b(?:(?:source|target|row|record|data)[_\s-]*)?(?:row[_\s-]*)?"
            r"count(?:\s*\(\s*\))?\s*[:=|]\s*(\d+)|"
            r"\b(?:source|target|rows?)\s*[:=|]\s*(\d+)", line, re.I)
        values.extend(int(n) for group in labeled for n in group if n)
        numeric_cells = line.strip().strip("|").strip()
        if not labeled and re.fullmatch(r"\d+(?:[\s|,]+\d+)*", numeric_cells):
            values.extend(int(n) for n in re.findall(r"\b\d+\b", numeric_cells))
    return values


def receipt_covers(sentences, pairs, command_text, exit_ok_re) -> bool:
    """Require command output that directly supports a pipeline or data claim."""
    for sentence in sentences:
        if DBT_CLAIM_RE.search(sentence):
            claim = re.search(r"\bdbt\s+(build|run|test)\b", sentence, re.I)
            action = claim.group(1) if claim else None
            if not any(
                (not action or re.search(rf"\bdbt\s+{action}\b", command_text(call), re.I))
                and re.search(r"\bdbt\s+(?:build|run|test)\b", command_text(call), re.I)
                and re.search(r"Done\.\s*PASS\s*=\s*\d+[^\n]*\bERROR\s*=\s*0\b", out, re.I)
                for call, out in pairs
            ):
                return False
        if BUILD_CLAIM_RE.search(sentence) and not DBT_CLAIM_RE.search(sentence):
            build_receipt = lambda call, out: (
                re.search(r"\bbuild\b", command_text(call), re.I)
                and (exit_ok_re.search(out)
                     or re.search(r"build\s+(?:succeeded|completed|passed|clean)", out, re.I)))
            if not any(build_receipt(call, out) for call, out in pairs):
                return False
        if PIPELINE_CLAIM_RE.search(sentence) and not re.search(r"\btest\s+run\b", sentence, re.I):
            def has_status_read(call, out):
                command = command_text(call)
                readback = re.search(r"\b(?:status|show|describe|get|list|runs?|jobs?|pipelines?)\b", command, re.I)
                run_id = re.search(
                    r"[\"']?\b(?:run[_ -]?id|execution[_ -]?id|executionarn|id)\b[\"']?\s*"
                    r"[:=]\s*[\"']?[\w./:-]+", out, re.I)
                success = re.search(r"\b(?:succeeded|completed|success)\b", out, re.I)
                return readback and run_id and success
            if not any(has_status_read(call, out) for call, out in pairs):
                return False
        if COMPILE_CLAIM_RE.search(sentence):
            claim = re.search(r"\b(?:compiled|compile|parse)\b", sentence, re.I)
            action = "parse" if claim and claim.group(0).lower() == "parse" else "compile"
            def has_compile(call, out):
                command = command_text(call)
                is_compile = re.search(rf"\b(?:dbt\s+)?{action}\b|\btsc(?:\s|$)", command, re.I)
                success = exit_ok_re.search(out) or re.search(
                    r"completed successfully|successfully parsed", out, re.I)
                return is_compile and success
            if not any(has_compile(call, out) for call, out in pairs):
                return False
        if DATA_CLAIM_RE.search(sentence):
            expected = []
            tail = sentence[DATA_CLAIM_RE.search(sentence).end():]
            counts = re.match(r"\s*[:=]?\s*(\d+(?:\s*(?:and|,|vs\.?)\s*\d+)*)", tail, re.I)
            if counts:
                expected = [int(n) for n in re.findall(r"\d+", counts.group(1))]
            observed = []
            for call, out in pairs:
                if re.search(r"\bselect\b[\s\S]*\bcount\s*\(", command_text(call), re.I):
                    observed.extend(_count_values(out))
            if expected and not all(Counter(observed)[n] >= count for n, count in Counter(expected).items()):
                return False
            if not expected and not any(count >= 2 for count in Counter(observed).values()):
                return False
        if VIETNAMESE_RUN_CLAIM_RE.search(sentence):
            if not any(exit_ok_re.search(out) for _, out in pairs):
                return False
    return True


MUTATING_TOOL_NAMES = {
    "run_command", "bash", "exec", "terminal", "cmd", "command",
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "edit_file", "create_file", "apply_diff", "patch", "modify_file",
    "write_file", "write", "edit", "multiedit", "notebook_edit", "notebookedit",
}


def has_execution_or_edit(steps) -> bool:
    for step in steps or []:
        calls = step.get("tool_calls") if isinstance(step, dict) else None
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict) and str(call.get("name") or "").strip().lower() in MUTATING_TOOL_NAMES:
                return True
    return False
