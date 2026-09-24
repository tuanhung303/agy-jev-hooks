"""sage.jev.verdict.support - Local support composition for Compass labels.

A hard label is never emitted as a bare allegation: the steer carries support
composed here, either an observed failure receipt or an explicitly scoped
claim-to-capture gap. Labels with no support are dropped; with no supported
label the whole steer is suppressed. Stdlib only, no classification.
"""
import re
from typing import Any, Dict, List, Optional

from sage.claims import TEST_CLAIM_RE, assertion_claims, claim_targets, host_of

# Absence-type labels accept a scoped gap; every other label needs an observed
# failure.
GAP_LABELS = frozenset({
    "not_verified", "undone", "premature_stop", "delivery_condition",
    "blast_radius_unchecked", "faked_evidence",
})
_STATUS_RE = re.compile(r"\[exit=([^\]\n]+)\]")
# A failure summary stands alone; a bare error keyword only counts when the
# receipt also recorded a non-zero status. Unknown status is neutral.
_FAILURE_SUMMARY_RE = re.compile(
    r"\b[1-9]\d*\s+(?:failed|failing|errors?)\b|\berror(?:s)? during collection\b|"
    r"\bno tests ran\b|#\s*fail\s+[1-9]\d*", re.I)
_FAILED_TOKEN_RE = re.compile(r"\bFAILED\b")
_ERROR_KEYWORD_RE = re.compile(r"\b(?:Traceback|AssertionError|SyntaxError|ModuleNotFoundError)\b")
_RUNNER_RESULT_RE = re.compile(
    r"\b\d+\s+(?:passed|failed|passing|failing)\b|\bFAILED\b|\bexit=0\b|#\s*(?:pass|fail)\s+\d+", re.I)
_NOISE_COMMAND_RE = re.compile(
    r"\b(?:tail|head|cat|bat|less|wc)\b[^\n]*\.jsonl|\bUpdated task #\d+ status\b", re.I)


def bounded(text: Any, cap: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= cap else flat[:cap - 3] + "..."


def _receipt_paragraphs(blocks: Dict[str, str]) -> List[str]:
    return [p for p in str(blocks.get("command_receipts") or "").split("\n\n") if p.strip()]


def _receipt_lines(paragraph: str) -> List[str]:
    return [line.strip() for line in paragraph.splitlines()
            if line.strip() and not line.strip().startswith("=== ")]


def _failure_line(lines: List[str]) -> str:
    for line in lines:
        if line.startswith(("[", "$ ")):
            continue
        if _FAILURE_SUMMARY_RE.search(line) or _FAILED_TOKEN_RE.search(line):
            return line
    return ""


def _failing_observation(blocks: Dict[str, str]) -> Optional[str]:
    """A receipt that shows an actual failure. A status alone never is one."""
    for paragraph in _receipt_paragraphs(blocks):
        lines = _receipt_lines(paragraph)
        if not lines or _NOISE_COMMAND_RE.search(lines[0]):
            continue
        status = _STATUS_RE.search(lines[1]) if len(lines) > 1 else None
        bad_status = bool(status) and status.group(1).lower() not in ("0", "unknown")
        signal = _failure_line(lines[1:])
        if not signal and bad_status:
            signal = next((line for line in lines[1:] if _ERROR_KEYWORD_RE.search(line)), "")
        if not signal:
            continue
        code = f" {status.group(0)}" if status else ""
        return f"command_receipts: {bounded(lines[0], 90)}{code} -> {bounded(signal, 120)}"
    for line in str(blocks.get("artifact_diffs") or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("=== ", "# ")) and _failure_line([line]):
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
