"""Receipt checks for pipeline and data completion claims."""
import re

from sage.claim_receipts import (  # noqa: F401  (has_execution_or_edit re-exported)
    INSPECTION_RE as _INSPECTION_RE, SCRIPT_RE, _cmd_text as _call_text, _program, _stages,
    has_execution_or_edit, is_execution,
)
from sage.jev.evidence.attribution import status_of
from sage.pipeline_counts import (  # noqa: F401  (DATA_CLAIM_RE re-exported)
    DATA_CLAIM_RE, counts_cover as _counts_cover, is_count_query as _is_count_query,
    sql_content_for as _sql_content_for,
)
from sage.pipeline_receipts import (
    clean as _clean, run_observation as _run_observation, status_body as _status_body,
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
    return bool(re.search(r"\b(?:status|show|view|watch|describe|get|list)\b", command, re.I))


def _is_api_read(command):
    return bool(re.search(r"/jobs/instances/|/dagRuns\b", command, re.I)
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


_RUN_STATUS_TEXT_RE = re.compile(
    r"\b(?:status|succeeded|completed|failed|in ?progress|running|queued)\b|"
    r"\b(?:monitor|poll|wait|watch)", re.I)


def operation_linked(sentence, command, output):
    """The wrapper's own command or output names the operation this claim asserts."""
    text = f"{command}\n{_status_body(output)}"
    if DBT_CLAIM_RE.search(sentence):
        return bool(re.search(r"\bdbt\b", text, re.I))
    if COMPILE_CLAIM_RE.search(sentence):
        return bool(re.search(r"\b(?:compile|parse|tsc|py_compile|compileall)\b", text, re.I))
    if BUILD_CLAIM_RE.search(sentence):
        return bool(re.search(r"\bbuild\b", text, re.I))
    if PIPELINE_CLAIM_RE.search(sentence):
        return bool(_RUN_STATUS_TEXT_RE.search(text))
    return bool(re.search(r"\b(?:count|select|recon\w*|sqlcmd|bq)\b", text, re.I))


def _unauditable_wrapper(pairs, sentence=""):
    for call, output, result in pairs:
        command = _call_text(call)
        if not is_execution(call) or not operation_linked(sentence, command, output):
            continue
        if SCRIPT_RE.search(command) and (
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


def receipt_covers(sentences, pairs, command_text, exit_ok_re, strict=False) -> bool:
    """Require matching receipts, or abstain when a linked wrapper cannot be audited.

    strict=True accepts only a positive receipt (used for carry-over evidence).
    """
    for sentence in sentences:
        # Unknown wrappers make absence inconclusive, but never erase an
        # explicit result for the operation named in this claim.
        uncertain_wrapper = not strict and _unauditable_wrapper(pairs, sentence) and not any(
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
            if covered is None and not strict:
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
            if counts_result is None and not strict:
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
