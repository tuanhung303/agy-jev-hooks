"""Receipt checks for pipeline and data completion claims."""
import re

from sage.claim_receipts import (  # noqa: F401  (has_execution_or_edit re-exported)
    INSPECTION_RE as _INSPECTION_RE, SCRIPT_RE, _cmd_text as _call_text, _program, _stages,
    has_execution_or_edit, is_execution,
)
from sage.jev.evidence.attribution import status_of
from sage.pipeline_receipts import (
    clean as _clean, count_records as _count_records,
    count_scalar_values as _count_scalar_values, run_observation as _run_observation,
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
    """True on a success record for the claimed run, None when unknown, else False."""
    wanted_id = _claimed_run_id(sentence)
    unknown = False
    for call, output, result in pairs:
        command = command_text(call)
        # agy task status reads describe the agent's own task, not the pipeline.
        if str(call.get("name") or "").lower() == "manage_task" or not (
                _is_status_read(command) or _is_api_read(command)):
            continue
        if not _status_ok(output, result):
            continue
        observed = _run_observation(output, wanted_id)
        if observed == "success":
            return True
        unknown = unknown or observed == "unknown"
    return None if unknown else False


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
    unassociated = False
    for call, output, result_step in pairs:
        command = command_text(call)
        if not _is_count_query(call, pairs, command_text):
            continue
        if not _status_ok(output, result_step):
            # A failed query observes nothing; a later successful retry decides.
            continue
        count_query_seen = True
        found, difference = _count_records(output)
        if found is None:
            unassociated = True
            continue
        usable_observation = usable_observation or bool(found or difference is not None)
        if difference == 0:
            zero_difference = True
        elif difference is not None:
            return False
        complete = [r for r in found if ("source" in r and "target" in r)
                    or ("row" in r and "record" in r)]
        for record in complete:
            left, right = (record["source"], record["target"]) if "source" in record \
                else (record["row"], record["record"])
            if left != right:
                return False
            observed.append((left, right))
        partial = [r for r in found if r not in complete]
        if len(found) > 1 and partial:
            # Several records, some one-sided: which sides compare is unknown.
            unassociated = True
            continue
        for record in partial:
            if "source" in record:
                observed.append((record["source"], None))
            elif "target" in record:
                observed.append((None, record["target"]))
        if not found and "source" in command.lower():
            vals = _count_scalar_values(output)
            if vals:
                usable_observation = True
                observed.append((int(vals[0]), None))
        elif not found and "target" in command.lower():
            vals = _count_scalar_values(output)
            if vals:
                usable_observation = True
                observed.append((None, int(vals[0])))
    if unassociated:
        return None
    sources = [source for source, _ in observed if source is not None]
    targets = [target for _, target in observed if target is not None]
    pair = (sources[0], targets[0]) if sources and targets else None
    consistent = bool(pair and len(set(sources)) == 1 and len(set(targets)) == 1
                      and pair[0] == pair[1] and (expected is None or pair == expected))
    rows_equal = bool(observed) and expected is None and all(
        left is not None and left == right for left, right in observed)
    if consistent or rows_equal or bool(zero_difference and (expected is None or expected[0] == expected[1])):
        return True
    if count_query_seen and not usable_observation:
        return None
    if count_query_seen and (not sources or not targets):
        return None
    return False


def _unauditable_wrapper(pairs):
    for call, output, result in pairs:
        command = _call_text(call)
        if is_execution(call) and SCRIPT_RE.search(command) and (
                result.get("_capture_ambiguous") or (output and _status_ok(output, result))):
            return True
        if re.search(r"\b(?:python3?\s+-c|node\s+-e|ruby\s+-e|perl\s+-e)\b", command, re.I) \
                and not _is_api_read(command):
            if output:
                return True
        if re.search(r"\bsqlcmd\s+-i\b", command, re.I) and not _sql_content_for(command, pairs, _call_text):
            if output:
                return True
    return False


def receipt_covers(sentences, pairs, command_text, exit_ok_re) -> bool:
    """Require matching receipts, or abstain when a wrapper cannot be audited."""
    for sentence in sentences:
        # Unknown wrappers make absence inconclusive, but never erase an
        # explicit result for the operation named in this claim.
        uncertain_wrapper = _unauditable_wrapper(pairs) and not any(
            is_execution(call) and attempts_operation(sentence, command_text(call), pairs)
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
            covered = _readback_covers(sentence, pairs, command_text)
            if covered is None:
                continue
            if not covered:
                if uncertain_wrapper:
                    continue
                return False
        if COMPILE_CLAIM_RE.search(sentence):
            # The action is the keyword of the matched claim; compile implies parse.
            keyword = COMPILE_CLAIM_RE.search(sentence).group(0).split()[0].lower()
            action = "(?:parse|compile)" if keyword == "parse" else "compile"
            def compile_receipt(call, output, result):
                command = command_text(call)
                valid_command = bool(re.search(
                    rf"\b(?:dbt\s+)?{action}\b|\btsc(?:\s|$)|\b(?:py_compile|compileall)\b", command, re.I))
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


def attempts_operation(sentence, command, pairs):
    """An executed (non-inspection) shell stage performs the claimed operation."""
    return any(not _INSPECTION_RE.match(_program(stage))
               and _claim_command_match(sentence, stage, pairs)
               for stage in _stages(command))


def _claim_command_match(sentence, command, pairs):
    """Whether this receipt set has an explicit, auditable command for claim."""
    if DBT_CLAIM_RE.search(sentence):
        match = re.search(r"\bdbt\s+(build|run|test)\b", sentence, re.I)
        if match:
            return bool(re.search(rf"\bdbt\s+{match.group(1)}\b", command, re.I))
    if BUILD_CLAIM_RE.search(sentence):
        return bool(re.search(r"\bbuild\b", command, re.I))
    if COMPILE_CLAIM_RE.search(sentence):
        return bool(re.search(r"\b(?:compile|parse|tsc|py_compile|compileall)\b", command, re.I))
    if PIPELINE_CLAIM_RE.search(sentence):
        return _is_status_read(command) or _is_api_read(command)
    if DATA_CLAIM_RE.search(sentence):
        return any(_is_count_query(other_call, pairs, _call_text)
                   for other_call, _output, _result in pairs if other_call is not None)
    return False
