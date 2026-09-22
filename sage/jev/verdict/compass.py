"""sage.jev.verdict.compass - Sage Compass: Jev-routed verdict labels (pass / failed[reasons]).

Classifies a transcript against the hard category catalog and renders
category-bound narratives. Evidence assembly lives in sage.jev.evidence
(redaction, receipt binding, recency priorities).
"""
import http.client
import math
import time
from typing import Any, Dict, List, Optional

from sage.jev.config.catalog import COMPASS_CATEGORIES, axis_of, derive_verdicts
from sage.jev.evidence.assemble import (  # noqa: F401  (re-exported for callers/tests)
    ARTIFACT_OLDER_CHARS,
    BLOCK_BUDGETS,
    PAYLOAD_CHAR_CAP,
    PER_FILE_DIFF_CHARS,
    RECEIPT_FULL_CHARS,
    RECEIPT_TAIL_CHARS,
    _read_steps_bounded,
    assemble_evidence,
    build_payload,
)
from sage.jev.request.parser import build_request
from sage.jev.request.prompt_pair import extract_prompt_pair
from sage.jev.transport import _call_jev
from sage.locking import log_audit
from sage.sanitizer import redact_secrets
from sage.user_context import has_stated_requirement
from sage.turnmode import session_mode
from sage.config import COMPASS_ENABLED, JEV_GATE_API_KEY

CLASSIFY_TIME_BUDGET_S = 10.0


def _evidence_snippet(blocks: Dict[str, str]) -> str:
    """First concrete observed line: receipts beat diffs beat the reply."""
    for name in ("command_receipts", "artifact_diffs", "final_reply"):
        for line in str(blocks.get(name) or "").splitlines():
            line = line.strip()
            if line and not line.startswith("=== "):
                return line[:120]
    return ""


def jev_compass_hint(transcript_path: str, deadline: Optional[float] = None) -> Optional[str]:
    """Single actionable steer: top hard label + its criterion + one evidence line.

    Nonbinding by design: hard-escalate labels focus the caller's inspection
    (it may reject them); remaining hard labels and quality notes go to the
    audit log, notes also ride the tail so the caller's length cap trims them
    first. Never blocks; None on any failure.
    """
    if not COMPASS_ENABLED or not JEV_GATE_API_KEY:
        return None
    result = jev_compass_classify(transcript_path, deadline=deadline)
    if result is None:
        return None
    hard = result.get("hard_escalate") or []
    notes = result.get("notes") or []
    if not hard:
        axes = result.get("axes") or {}
        by_axis = "; ".join(f"{axis}: {', '.join(labels) or 'none'}"
                            for axis, labels in sorted(axes.items()))
        log_audit(f"jev_compass: no hard labels fired (notes: {', '.join(notes) or 'none'}"
                  + (f"; axes {by_axis}" if by_axis else "") + ")")
        return None
    top = max(hard, key=lambda cat: result["fired"].get(cat, 0.0))
    spec = COMPASS_CATEGORIES.get(top) or {}
    log_audit(f"jev_compass hard: {', '.join(hard)}; top={top} axis={axis_of(top) or 'unknown'}")
    hint = f"jev_compass {top}: {spec.get('criterion', '')}".strip()
    snippet = _evidence_snippet(result.get("blocks") or {})
    if snippet:
        hint += f" Evidence: {snippet}"
    if notes:
        hint += f" Note: {', '.join(notes)}"
    log_audit(redact_secrets(hint))
    return hint


def _valid_probability(answer: Any) -> Optional[float]:
    """Accept only boolean-typed answers with finite probabilities in [0, 1]."""
    if not isinstance(answer, dict) or answer.get("type") != "boolean":
        return None
    score = answer.get("probability")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    try:
        out = float(score)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(out) or not 0.0 <= out <= 1.0:
        return None
    return out


def _compass_context(steps: List[Dict[str, Any]], blocks: Dict[str, str]) -> Dict[str, Any]:
    """Placeholder context for the yaml compass case: evidence plus the
    steering-excluded last prompt pair."""
    pair = extract_prompt_pair(list(steps))
    return {
        "evidence": build_payload(blocks),
        "last_user": pair["user"],
        "last_agent": pair["agent"],
    }


def jev_compass_classify(transcript_path: str, deadline: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Classify a transcript into catalog labels. Shadow-only; fails silent."""
    started = time.monotonic()
    deadline = min(deadline, started + CLASSIFY_TIME_BUDGET_S) if deadline is not None \
        else started + CLASSIFY_TIME_BUDGET_S
    try:
        steps = _read_steps_bounded(transcript_path)
        if not steps:
            log_audit("jev_compass unavailable: empty transcript")
            return None
        if not has_stated_requirement(steps):
            log_audit("jev_compass skipped: no stated user requirement")
            return None
        mode = session_mode(steps)
        if mode != "work":
            log_audit(f"jev_compass skipped: {mode} turn")
            return None
        blocks = assemble_evidence(steps)
        payload = build_payload(blocks)
        try:
            body = build_request("compass", _compass_context(steps, blocks))
        except ValueError as exc:
            log_audit(f"jev_compass skipped: request not buildable ({type(exc).__name__})")
            return None
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining < 1.0:
            log_audit("jev_compass skipped: no remaining budget")
            return None
        attempt_timeout = 8.0 if remaining is None else max(1.0, min(8.0, remaining))
        data = _call_jev(body["state"], body["questions"], attempt_timeout=attempt_timeout, deadline=deadline)
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict) or not answers:
            raise ValueError("missing or empty answers record")
        labels: Dict[str, float] = {}
        for cat_id in COMPASS_CATEGORIES:
            score = _valid_probability(answers.get(cat_id))
            if score is not None:
                labels[cat_id] = score
        if not labels:
            raise ValueError("no parsable category answers")
        verdicts = derive_verdicts(labels)
        usage = data.get("usage") or {}
        return {
            "labels": labels,
            "coverage": f"{len(labels)}/{len(COMPASS_CATEGORIES)}",
            "completion_confidence": _valid_probability(answers.get("q_pass")),
            **verdicts,
            "payload_chars": len(payload),
            "blocks": blocks,
            "tokens": {"input": usage.get("inputTokens"), "output": usage.get("outputTokens")},
            "latency_s": round(time.monotonic() - started, 2),
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError,
            OverflowError, http.client.HTTPException) as exc:
        log_audit(f"jev_compass unavailable: {type(exc).__name__}")
        return None


def render_briefs(result: Dict[str, Any]) -> List[str]:
    """Closed narratives for fired labels; unfilled slots stay <unassigned>."""
    briefs = []
    for cat_id in sorted(result.get("fired", {})):
        spec = COMPASS_CATEGORIES[cat_id]
        narrative = spec["narrative"].format(
            E="<unassigned>", R="<unassigned>", T="<unassigned>",
            C="<unassigned>", K="<unassigned>", D="<unassigned>",
        )
        briefs.append(f"[{cat_id}|route {spec['route']}] {narrative}")
    return briefs
