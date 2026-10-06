"""sage.jev.verdict.support - Local support composition for Compass labels.

A hard label is never emitted as a bare allegation: the steer carries support
composed here, either an observed failure receipt or an explicitly scoped
claim-to-capture gap. Labels with no support are dropped; with no supported
label the whole steer is suppressed. Stdlib only, no classification.
"""
import re
from typing import Any, Dict, List, Optional

from sage.claims import (
    BUILD_CLAIM_RE, COMPILE_CLAIM_RE, DATA_CLAIM_RE, DBT_CLAIM_RE,
    PIPELINE_CLAIM_RE, TEST_CLAIM_RE, _same_scope, assertion_claims, claim_targets, host_of,
)
from sage.claim_assertions import _claim_sentences

# Absence-type labels accept a scoped gap; every other label needs an observed
# failure.
GAP_LABELS = frozenset({
    "not_verified", "undone", "premature_stop", "delivery_condition",
    "blast_radius_unchecked", "faked_evidence",
})
# A failure summary stands alone; a bare error keyword only counts when the
# receipt also recorded a non-zero status. Unknown status is neutral.
_FAILURE_SUMMARY_RE = re.compile(
    r"\b[1-9]\d*\s+(?:failed|failing|errors?)\b|\berror(?:s)? during collection\b|"
    r"\bno tests ran\b|#\s*fail\s+[1-9]\d*", re.I)
_FAILED_TOKEN_RE = re.compile(r"\bFAILED\b")
# Code that names a status ('"FAILED"') handles failures; it is not one.
_QUOTED_STATUS_RE = re.compile(r"""["']FAILED["']""")
# A check result that expected the failure and saw it: "expected FAILED, actual FAILED, pass True".
_PASS_VERDICT_RE = re.compile(r"(?i:\bpass['\"]?\s*[:=]\s*true\b)|\bTrue\s*$")
_ERROR_KEYWORD_RE = re.compile(
    r"\b(?:Compilation Error|Traceback|AssertionError|SyntaxError|ModuleNotFoundError)\b")
_RUNNER_RESULT_RE = re.compile(
    r"\b\d+\s+(?:passed|failed|passing|failing)\b|\bFAILED\b|\bexit=0\b|#\s*(?:pass|fail)\s+\d+", re.I)
_NOISE_COMMAND_RE = re.compile(
    r"\b(?:tail|head|cat|bat|less|wc)\b[^\n]*\.jsonl|\bUpdated task #\d+ status\b", re.I)
_DBT_SUMMARY_RE = re.compile(r"\bDone\.[^\n]*\bPASS\s*=\s*\d+[^\n]*\bERROR\s*=\s*(\d+)", re.I)


def bounded(text: Any, cap: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= cap else flat[:cap - 3] + "..."


def _receipt_paragraphs(blocks: Dict[str, str]) -> List[str]:
    return [p for p in str(blocks.get("command_receipts") or "").split("\n\n") if p.strip()]


def _failure_line(lines: List[str]) -> str:
    for line in lines:
        if line.startswith(("[", "$ ")) or _PASS_VERDICT_RE.search(line):
            continue
        if _FAILURE_SUMMARY_RE.search(line) or _FAILED_TOKEN_RE.search(line):
            return line
    return ""


def _receipt_fields(paragraph: str):
    """Keep multiline command, status, and output in separate fields."""
    lines = [line.rstrip() for line in paragraph.splitlines() if line.strip()
             and not line.strip().startswith("=== ")]
    if not lines or not lines[0].lstrip().startswith("$ "):
        return "", "unknown", []
    status_index = next((i for i, line in enumerate(lines)
                         if re.fullmatch(r"\[exit=[^\]\n]+\]", line.strip())), None)
    if status_index is None:
        return "\n".join(lines).removeprefix("$ "), "unknown", []
    command_lines = lines[:status_index]
    command_lines[0] = command_lines[0].lstrip()[2:]
    command = "\n".join(command_lines).strip()
    status = lines[status_index].strip()[6:-1]
    return "$ " + command, status, lines[status_index + 1:]


def _normalize_command(command: str) -> str:
    value = re.sub(r"\s+", " ", str(command or "")).strip().casefold()
    value = re.sub(r"^(?:uv\s+run\s+)+", "", value)
    value = re.sub(r"^(?:python|python3)\s+-m\s+", "", value)
    return value


def _same_runner_scope(left: str, right: str) -> bool:
    a, b = _normalize_command(left), _normalize_command(right)
    if a == b:
        return True
    runner = re.compile(r"\b(pytest|unittest|jest|vitest|mocha|rspec|phpunit|tox|cargo\s+test|go\s+test)\b")
    ma, mb = runner.search(a), runner.search(b)
    return bool(ma and mb and ma.group(1) == mb.group(1)
                and a[ma.end():].strip() == b[mb.end():].strip())


def _claim_matches_command(claim: str, command: str) -> bool:
    command = str(command or "")
    paths, urls = claim_targets(claim)
    if paths and not _same_scope(command, paths):
        return False
    if urls and not any(host and host in command.casefold() for host in
                        (host_of(url) for url in urls)):
        return False
    if DBT_CLAIM_RE.search(claim):
        action = re.search(r"\bdbt\s+(build|run|test)\b", claim, re.I)
        return bool(action and re.search(rf"\bdbt\s+{action.group(1)}\b", command, re.I))
    if TEST_CLAIM_RE.search(claim):
        return bool(re.search(r"\b(?:pytest|unittest|jest|vitest|mocha|rspec|phpunit|tox|cargo test|go test)\b",
                              command, re.I))
    if COMPILE_CLAIM_RE.search(claim):
        action = re.search(r"\b(compile|parse|compiled)\b", claim, re.I)
        term = "parse" if action and action.group(1).lower() == "parse" else "compile"
        return bool(re.search(rf"\b(?:dbt\s+)?{term}\b|\btsc(?:\s|$)", command, re.I))
    if DATA_CLAIM_RE.search(claim):
        return bool(re.search(r"\bselect\b[\s\S]*\bcount\s*\(", command, re.I))
    if PIPELINE_CLAIM_RE.search(claim):
        return bool(re.search(r"\b(?:status|show|view|describe|get|list)\b|/jobs/instances/", command, re.I))
    return True


def _failing_observation(blocks: Dict[str, str]) -> Optional[str]:
    """A receipt that shows an actual failure. A status alone never is one."""
    paragraphs = _receipt_paragraphs(blocks)
    reply = str(blocks.get("final_reply") or "")
    claims = assertion_claims(reply)
    for pattern in (DBT_CLAIM_RE, BUILD_CLAIM_RE, COMPILE_CLAIM_RE,
                    DATA_CLAIM_RE, PIPELINE_CLAIM_RE):
        claims.extend(_claim_sentences(reply, pattern))
    scoped_claim = claims[-1] if claims else ""
    for index, paragraph in enumerate(paragraphs):
        command, status_value, output_lines = _receipt_fields(paragraph)
        lines = output_lines
        if not lines or _NOISE_COMMAND_RE.search(lines[0]):
            continue
        status = status_value if status_value not in ("unknown", "") else ""
        bad_status = bool(status) and status.lower() not in ("0", "unknown")
        dbt_summary = _DBT_SUMMARY_RE.search("\n".join(lines))
        signal = "" if dbt_summary and int(dbt_summary.group(1)) == 0 else _failure_line(lines)
        if not signal and bad_status:
            signal = next((line for line in lines[1:] if _ERROR_KEYWORD_RE.search(line)), "")
        if not signal and dbt_summary and int(dbt_summary.group(1)) > 0:
            signal = dbt_summary.group(0)
        if not signal:
            continue
        command_key = _normalize_command(command)
        if scoped_claim and not _claim_matches_command(scoped_claim, command):
            continue
        # A later successful rerun of the same captured command supersedes
        # its earlier failure for current-turn support. Runner-equivalent
        # invocations such as pytest and uv run pytest share a scope.
        superseded = False
        for later in paragraphs[index + 1:]:
            later_command, later_status, later_output = _receipt_fields(later)
            if not later_command or not (command_key == _normalize_command(later_command)
                                         or _same_runner_scope(command, later_command)):
                continue
            later_summary = _DBT_SUMMARY_RE.search("\n".join(later_output))
            if later_status.lower() == "0" and not _failure_line(later_output) \
                    and not (later_summary and int(later_summary.group(1)) > 0):
                superseded = True
                break
        if superseded:
            continue
        code = f" [exit={status}]" if status else ""
        return f"command_receipts: {bounded(command, 90)}{code} -> {bounded(signal, 120)}"
    earlier_artifact = False
    for raw_line in str(blocks.get("artifact_diffs") or "").splitlines():
        if raw_line.startswith("# "):
            earlier_artifact = "[written in an earlier turn]" in raw_line
            continue
        line = raw_line.strip()
        if (not earlier_artifact and line and not line.startswith("=== ")
                and not _QUOTED_STATUS_RE.search(line) and _failure_line([line])):
            return f"artifact_diffs: {bounded(line, 160)}"
    return None


def _scoped_gap(claim: str, captured: str) -> Optional[str]:
    """What the captured receipts and diffs do not cover, with its locator."""
    paths, urls = claim_targets(claim)
    missing = [u for u in urls if host_of(u) not in captured]
    missing += [p for p in paths
                if p.split("/")[-1].rsplit(".", 1)[0].lower() not in captured.lower()]
    if missing:
        return "no captured receipt or diff covers " + ", ".join(dict.fromkeys(missing))[:200]
    if TEST_CLAIM_RE.search(claim) and not _RUNNER_RESULT_RE.search(captured):
        return "no runner result captured"
    return None


def _gap_observation(blocks: Dict[str, str]) -> Optional[str]:
    """A claim-to-capture gap, scoped and located; None when nothing is scoped."""
    receipts = "\n".join(_receipt_paragraphs(blocks))
    diffs = str(blocks.get("artifact_diffs") or "")
    captured = f"{receipts}\n{'' if diffs.startswith('<no ') else diffs}".strip()
    if not captured or captured.startswith("<no "):
        return None  # a truncated or empty capture cannot establish absence
    for claim in reversed(assertion_claims(str(blocks.get("final_reply") or ""))):
        claim = bounded(claim, 160)
        gap = _scoped_gap(claim, captured)
        if gap:
            return f'final_reply claims "{claim}"; {gap}'
    return None


def label_support(label: str, blocks: Dict[str, str]) -> Optional[str]:
    """Support genuinely connected to the label, or None: no support, no steer."""
    if label in GAP_LABELS:
        return _failing_observation(blocks) or _gap_observation(blocks)
    return _failing_observation(blocks)
