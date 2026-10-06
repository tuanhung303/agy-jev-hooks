"""Receipt checks for pipeline and data completion claims."""
import json
import re

from sage.jev.evidence.attribution import status_of

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

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SUCCESS_RE = re.compile(r"\b(?:succeeded|completed successfully|successfully parsed|build succeeded|build completed|build passed|build clean)\b", re.I)
_FAILURE_RE = re.compile(r"\b(?:failed|failure|error|errors|cancelled|canceled|aborted)\b", re.I)
_TERMINAL_SUCCESS = {"succeeded", "success", "completed", "complete"}
_TERMINAL_FAILURE = {"failed", "failure", "error", "cancelled", "canceled", "aborted"}


def _clean(output):
    return _ANSI_RE.sub("", str(output or ""))


def _status_ok(output):
    status = status_of({}, _clean(output))
    if status != "unknown":
        return status == "0"
    text = re.sub(r"\bERROR\s*=\s*0\b", "", _clean(output), flags=re.I)
    return not _FAILURE_RE.search(text)


def _dbt_summary(output):
    text = _clean(output)
    for match in re.finditer(r"\bDone\.", text, re.I):
        summary = text[match.end():match.end() + 500]
        passed = re.search(r"\bPASS\s*=\s*\d+\b", summary, re.I)
        errors = re.search(r"\bERROR\s*=\s*(\d+)\b", summary, re.I)
        if passed and errors:
            return int(errors.group(1)) == 0 and _status_ok(text)
    return False


def _terminal_status(output):
    text = _clean(output)
    try:
        value = json.loads(text)
        if isinstance(value, dict) and isinstance(value.get("status"), str):
            return value["status"].strip().lower()
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\bstatus\s*[:=]\s*[\"']?([\w-]+)", text, re.I)
    return match.group(1).strip().lower() if match else ""


def _run_id(output):
    text = _clean(output)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            for key in ("run_id", "execution_id", "executionArn", "id"):
                if value.get(key) is not None:
                    return str(value[key]).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(
        r"[\"']?\b(?:run[_ -]?id|execution[_ -]?id|executionarn|id)\b[\"']?\s*"
        r"[:=]\s*[\"']?([\w./:-]+)", text, re.I)
    return match.group(1).strip() if match else ""


def _claimed_run_id(sentence):
    match = re.search(r"\b(?:pipeline|job|run)[\s_-]+([\w.-]+)\s+"
                      r"(?:succeed|complete|pass|finish)", sentence, re.I)
    value = match.group(1) if match else ""
    return "" if value.lower() in {"run", "job", "pipeline"} else value


def _is_status_read(command):
    return bool(re.search(r"\b(?:status|show|describe|get|list|runs?|jobs?|pipelines?)\b", command, re.I))


def _is_api_read(command):
    return bool(re.search(r"/jobs/instances/", command, re.I)
                and re.search(r"\b(?:curl|wget|requests?\.(?:get|post)|urlopen|httpx\.(?:get|post))\b",
                              command, re.I))


def _readback_covers(sentence, pairs, command_text):
    wanted_id = _claimed_run_id(sentence)
    for call, output in pairs:
        command = command_text(call)
        api_read = _is_api_read(command)
        if not (_is_status_read(command) or api_read):
            continue
        status = _terminal_status(output)
        if status in _TERMINAL_FAILURE or status not in _TERMINAL_SUCCESS:
            continue
        observed_id = _run_id(output)
        if not observed_id or (wanted_id and observed_id.casefold() != wanted_id.casefold()):
            continue
        return True
    return False


def _count_observation(output):
    """Return labelled count observations and an explicit difference value."""
    text = _clean(output)
    values = {}
    difference = None
    for match in re.finditer(r"\b(source|target|row|record|data)[_\s-]*count\s*[:=|]\s*(\d+)", text, re.I):
        values[match.group(1).lower()] = int(match.group(2))
    diff = re.search(r"\b(?:difference|diff|mismatch(?:_count)?)\s*[:=|]\s*(\d+)", text, re.I)
    if diff:
        difference = int(diff.group(1))
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        labels = re.findall(r"\b(source|target)[_\s-]*count\b", line, re.I)
        numbers = re.findall(r"\b\d+\b", lines[index + 1])
        if len(labels) >= 2 and len(numbers) >= len(labels):
            values.update({label.lower(): int(number) for label, number in zip(labels, numbers)})
    return values, difference


def _sql_content_for(command, pairs, command_text):
    path = re.search(r"\bsqlcmd\s+-i\s+([^\s]+)", command, re.I)
    if not path:
        return ""
    wanted = path.group(1).strip("\"'").replace("\\", "/").rsplit("/", 1)[-1]
    for call, output in pairs:
        if str(call.get("name") or "").lower() not in {"read_file", "readfile", "view_file"}:
            continue
        source_path = command_text(call).replace("\\", "/").strip("\"'")
        source = source_path.rsplit("/", 1)[-1]
        if source_path == path.group(1).strip("\"'").replace("\\", "/") or (
                "/" not in source_path and source == wanted):
            return _clean(output)
    return ""


def _is_count_query(call, pairs, command_text):
    command = command_text(call)
    if re.search(r"\bselect\b[\s\S]*\bcount\s*\(", command, re.I):
        return True
    content = _sql_content_for(command, pairs, command_text)
    return bool(content and re.search(r"\bselect\b[\s\S]*\bcount\s*\(", content, re.I))


def _counts_cover(sentence, pairs, command_text):
    claim_match = DATA_CLAIM_RE.search(sentence)
    tail = sentence[claim_match.end():] if claim_match else ""
    expected_match = re.search(r"\s*[:=]?\s*(\d+)\s*(?:and|,|vs\.?)\s*(\d+)", tail, re.I)
    expected = tuple(map(int, expected_match.groups())) if expected_match else None
    if expected and expected[0] != expected[1]:
        return False
    observed = []
    zero_difference = False
    for call, output in pairs:
        command = command_text(call)
        if not _is_count_query(call, pairs, command_text) or not _status_ok(output):
            continue
        labels, difference = _count_observation(output)
        if difference == 0:
            zero_difference = True
        elif difference is not None:
            return False
        if "source" in labels and "target" in labels:
            if labels["source"] != labels["target"]:
                return False
            observed.append((labels["source"], labels["target"]))
        elif "row" in labels and "record" in labels:
            if labels["row"] != labels["record"]:
                return False
            observed.append((labels["row"], labels["record"]))
        elif "source" in command.lower():
            vals = re.findall(r"\b\d+\b", _clean(output))
            if vals:
                observed.append((int(vals[-1]), None))
        elif "target" in command.lower():
            vals = re.findall(r"\b\d+\b", _clean(output))
            if vals:
                observed.append((None, int(vals[-1])))
    sources = [source for source, _ in observed if source is not None]
    targets = [target for _, target in observed if target is not None]
    pair = (sources[0], targets[0]) if sources and targets else None
    consistent = bool(pair and len(set(sources)) == 1 and len(set(targets)) == 1
                      and pair[0] == pair[1] and (expected is None or pair == expected))
    return consistent or bool(zero_difference and (expected is None or expected[0] == expected[1]))


def _unauditable_wrapper(pairs):
    for call, output in pairs:
        command = str(call.get("args") or call.get("arguments") or "")
        if re.search(r"\b(?:python3?\s+-c|node\s+-e|ruby\s+-e|perl\s+-e)\b", command, re.I) \
                and not _is_api_read(command):
            if output:
                return True
        if re.search(r"\bsqlcmd\s+-i\b", command, re.I) and not _sql_content_for(command, pairs, _call_text):
            if output:
                return True
    return False


def _call_text(call):
    args = call.get("args") or call.get("arguments") or {}
    if isinstance(args, dict):
        for key in ("command", "Command", "cmd", "Cmd", "CommandLine", "command_line",
                    "commandLine", "script", "Script", "path", "file", "filename"):
            if isinstance(args.get(key), str):
                return args[key]
    return str(args)


def receipt_covers(sentences, pairs, command_text, exit_ok_re) -> bool:
    """Require matching receipts, or abstain when a wrapper cannot be audited."""
    if _unauditable_wrapper(pairs):
        return True
    for sentence in sentences:
        if DBT_CLAIM_RE.search(sentence):
            match = re.search(r"\bdbt\s+(build|run|test)\b", sentence, re.I)
            action = match.group(1) if match else None
            if not any((not action or re.search(rf"\bdbt\s+{action}\b", command_text(call), re.I))
                       and re.search(r"\bdbt\s+(?:build|run|test)\b", command_text(call), re.I)
                       and _dbt_summary(output) for call, output in pairs):
                return False
        if BUILD_CLAIM_RE.search(sentence) and not DBT_CLAIM_RE.search(sentence):
            if not any(re.search(r"\bbuild\b", command_text(call), re.I)
                       and _status_ok(output)
                       and (status_of({}, _clean(output)) == "0" or _SUCCESS_RE.search(_clean(output)))
                       for call, output in pairs):
                return False
        if PIPELINE_CLAIM_RE.search(sentence):
            if re.search(r"\btest\s+run\b", sentence, re.I):
                continue
            if not _readback_covers(sentence, pairs, command_text):
                return False
        if COMPILE_CLAIM_RE.search(sentence):
            match = re.search(r"\b(?:compiled|compile|parse)\b", sentence, re.I)
            action = "parse" if match and match.group(0).lower() == "parse" else "compile"
            def compile_receipt(call, output):
                command = command_text(call)
                valid_command = bool(re.search(rf"\b(?:dbt\s+)?{action}\b|\btsc(?:\s|$)", command, re.I))
                status = status_of({}, _clean(output))
                success = status == "0" or (status == "unknown" and _SUCCESS_RE.search(_clean(output)))
                return valid_command and success and not _FAILURE_RE.search(_clean(output))
            if not any(compile_receipt(call, output) for call, output in pairs):
                return False
        if DATA_CLAIM_RE.search(sentence) and not _counts_cover(sentence, pairs, command_text):
            return False
        if VIETNAMESE_RUN_CLAIM_RE.search(sentence):
            action = re.search(r"\bdbt\s+(build|run|test)\b", sentence, re.I)
            if not action or not any(
                re.search(rf"\bdbt\s+{action.group(1)}\b", command_text(call), re.I)
                and _dbt_summary(output) for call, output in pairs):
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
