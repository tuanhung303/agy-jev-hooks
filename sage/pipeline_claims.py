"""Receipt checks for pipeline and data completion claims."""
import re

from sage.jev.evidence.attribution import status_of
from sage.pipeline_receipts import (
    clean as _clean, count_observation as _count_observation,
    count_scalar_values as _count_scalar_values, run_id as _run_id,
    terminal_status as _terminal_status,
)

DBT_CLAIM_RE = re.compile(
    r"\bdbt\s+(?:build|run|test)\b.{0,50}\b(?:pass(?:ed)?|succeed(?:ed)?|completed|clean)\b|"
    r"\bPASS\s*=\s*\d+[^\n]{0,80}\bERROR\s*=\s*0\b|"
    r"\bđã\s+chạy\s+xong.{0,30}\bdbt\s+(?:build|run|test)\b|"
    r"\bdbt\s+(?:build|run|test)\b.{0,30}\bđã\s+chạy\s+xong\b", re.I)
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
_SUCCESS_RE = re.compile(r"\b(?:succeeded|completed successfully|successfully parsed|build succeeded|build completed|build passed|build clean)\b", re.I)
_FAILURE_RE = re.compile(r"\b(?:failed|failure|error|errors|cancelled|canceled|aborted)\b", re.I)
_TERMINAL_SUCCESS = {"succeeded", "success", "completed", "complete"}
_TERMINAL_FAILURE = {"failed", "failure", "error", "cancelled", "canceled", "aborted"}
_COMPILE_FAILURE_RE = re.compile(
    r"\bCompilation Error\b|\b(?:error|diagnostic)\s+code\s*[:#=]?\s*[1-9]\d*\b|"
    r"\bCould not find (?:ref|model|source)\b", re.I)


def _status_ok(output, result_step=None):
    status = status_of(result_step or {}, _clean(output))
    if status != "unknown":
        return status == "0"
    text = re.sub(r"\bERROR\s*=\s*0\b", "", _clean(output), flags=re.I)
    return not _FAILURE_RE.search(text)


def _dbt_summary(output, result_step=None):
    text = _clean(output)
    for match in re.finditer(r"\bDone\.", text, re.I):
        summary = text[match.end():match.end() + 500]
        passed = re.search(r"\bPASS\s*=\s*\d+\b", summary, re.I)
        errors = re.search(r"\bERROR\s*=\s*(\d+)\b", summary, re.I)
        if passed and errors:
            return int(errors.group(1)) == 0 and _status_ok(text, result_step)
    return False


def _claimed_run_id(sentence):
    match = re.search(r"\b(?:pipeline|job|run)[\s_-]+([\w.-]+)\s+"
                      r"(?:succeed|complete|pass|finish)", sentence, re.I)
    value = match.group(1) if match else ""
    return "" if value.lower() in {"run", "job", "pipeline"} else value


def _is_status_read(command):
    return bool(re.search(r"\b(?:status|show|view|describe|get|list)\b", command, re.I))


def _is_api_read(command):
    return bool(re.search(r"/jobs/instances/", command, re.I)
                and re.search(r"\b(?:curl|wget|requests?\.(?:get|post)|urlopen|httpx\.(?:get|post))\b",
                              command, re.I))


def _readback_covers(sentence, pairs, command_text):
    wanted_id = _claimed_run_id(sentence)
    for call, output, _result in pairs:
        command = command_text(call)
        api_read = _is_api_read(command)
        if not (_is_status_read(command) or api_read):
            continue
        if not _status_ok(output, _result):
            continue
        status = _terminal_status(output)
        if status in _TERMINAL_FAILURE or status not in _TERMINAL_SUCCESS:
            continue
        observed_id = _run_id(output)
        if not observed_id or (wanted_id and observed_id.casefold() != wanted_id.casefold()):
            continue
        return True
    return False


def _sql_content_for(command, pairs, command_text):
    path = re.search(r"\bsqlcmd\s+-i\s+([^\s]+)", command, re.I)
    if not path:
        return ""
    wanted = path.group(1).strip("\"'").replace("\\", "/").rsplit("/", 1)[-1]
    for call, output, _result in pairs:
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
    count_query_seen = False
    usable_observation = False
    for call, output, result_step in pairs:
        command = command_text(call)
        if not _is_count_query(call, pairs, command_text):
            continue
        count_query_seen = True
        if not _status_ok(output, result_step):
            return False
        labels, difference = _count_observation(output)
        usable_observation = usable_observation or bool(labels or difference is not None)
        if difference == 0:
            zero_difference = True
        elif difference is not None:
            return False
        if "source" in labels and "target" in labels:
            if labels["source"] != labels["target"]:
                return False
            observed.append((labels["source"], labels["target"]))
        elif "source" in labels:
            observed.append((labels["source"], None))
        elif "target" in labels:
            observed.append((None, labels["target"]))
        elif "row" in labels and "record" in labels:
            if labels["row"] != labels["record"]:
                return False
            observed.append((labels["row"], labels["record"]))
        elif "source" in command.lower():
            vals = _count_scalar_values(output)
            if vals:
                usable_observation = True
                observed.append((int(vals[0]), None))
        elif "target" in command.lower():
            vals = _count_scalar_values(output)
            if vals:
                usable_observation = True
                observed.append((None, int(vals[0])))
    sources = [source for source, _ in observed if source is not None]
    targets = [target for _, target in observed if target is not None]
    pair = (sources[0], targets[0]) if sources and targets else None
    consistent = bool(pair and len(set(sources)) == 1 and len(set(targets)) == 1
                      and pair[0] == pair[1] and (expected is None or pair == expected))
    if consistent or bool(zero_difference and (expected is None or expected[0] == expected[1])):
        return True
    if count_query_seen and not usable_observation:
        return None
    if count_query_seen and (not sources or not targets):
        return None
    return False


def _unauditable_wrapper(pairs):
    for call, output, _result in pairs:
        command = _call_text(call)
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
    for sentence in sentences:
        # Unknown wrappers make absence inconclusive, but never erase an
        # explicit result for the operation named in this claim.
        uncertain_wrapper = _unauditable_wrapper(pairs) and not any(
            _claim_command_match(sentence, command_text(call), pairs)
            and not re.search(r"\b(?:rg|grep|cat|sed|head|tail|less|more|ls|stat)\b",
                              command_text(call), re.I)
            for call, _output, _result in pairs)
        if DBT_CLAIM_RE.search(sentence):
            match = re.search(r"\bdbt\s+(build|run|test)\b", sentence, re.I)
            action = match.group(1) if match else None
            if not any((not action or re.search(rf"\bdbt\s+{action}\b", command_text(call), re.I))
                       and re.search(r"\bdbt\s+(?:build|run|test)\b", command_text(call), re.I)
                       and _dbt_summary(output, result) for call, output, result in pairs):
                if uncertain_wrapper:
                    continue
                return False
        if BUILD_CLAIM_RE.search(sentence) and not DBT_CLAIM_RE.search(sentence):
            if not any(re.search(r"\bbuild\b", command_text(call), re.I)
                       and _status_ok(output, result)
                       and (status_of(result, _clean(output)) == "0" or _SUCCESS_RE.search(_clean(output)))
                       for call, output, result in pairs):
                if uncertain_wrapper:
                    continue
                return False
        if PIPELINE_CLAIM_RE.search(sentence):
            if re.search(r"\btest\s+run\b", sentence, re.I):
                continue
            if not _readback_covers(sentence, pairs, command_text):
                if uncertain_wrapper:
                    continue
                return False
        if COMPILE_CLAIM_RE.search(sentence):
            match = re.search(r"\b(?:compiled|compile|parse)\b", sentence, re.I)
            action = "parse" if match and match.group(0).lower() == "parse" else "compile"
            def compile_receipt(call, output, result):
                command = command_text(call)
                valid_command = bool(re.search(rf"\b(?:dbt\s+)?{action}\b|\btsc(?:\s|$)", command, re.I))
                status = status_of(result, _clean(output))
                text = _clean(output)
                count = re.search(r"\b(?:found\s+)?(\d+)\s+errors?\b", text, re.I)
                positive_errors = bool(count and int(count.group(1)) > 0)
                zero_errors = bool(count and int(count.group(1)) == 0)
                success = status == "0" or (status == "unknown" and (
                    _SUCCESS_RE.search(text) or zero_errors))
                return (valid_command and success and not positive_errors
                        and not _COMPILE_FAILURE_RE.search(text) and status not in ("error",))
            if not any(compile_receipt(call, output, result) for call, output, result in pairs):
                if uncertain_wrapper:
                    continue
                return False
        if DATA_CLAIM_RE.search(sentence):
            counts_result = _counts_cover(sentence, pairs, command_text)
            if counts_result is None:
                continue
            if not counts_result:
                if uncertain_wrapper:
                    continue
                return False
    return True


def _claim_command_match(sentence, command, pairs):
    """Whether this receipt set has an explicit, auditable command for claim."""
    if DBT_CLAIM_RE.search(sentence):
        match = re.search(r"\bdbt\s+(build|run|test)\b", sentence, re.I)
        if match:
            return bool(re.search(rf"\bdbt\s+{match.group(1)}\b", command, re.I))
    if BUILD_CLAIM_RE.search(sentence):
        return bool(re.search(r"\bbuild\b", command, re.I))
    if COMPILE_CLAIM_RE.search(sentence):
        return bool(re.search(r"\b(?:compile|parse|tsc)\b", command, re.I))
    if PIPELINE_CLAIM_RE.search(sentence):
        return _is_status_read(command) or _is_api_read(command)
    if DATA_CLAIM_RE.search(sentence):
        return any(_is_count_query(other_call, pairs, _call_text)
                   for other_call, _output, _result in pairs if other_call is not None)
    return False


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
