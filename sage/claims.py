"""
sage.claims - Claim to receipt contract (layer 3).

A completion claim counts only with a matching receipt: test claims need a
test-run receipt with exit=0, deploy claims a URL/status receipt, visual-proof
claims a clean screenshot. Missing kind is an accurate not_verified, not a nag.
No transcript read: fail open, never guess.
"""
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sage.jev.config.catalog import COMPASS_CATEGORIES

TEST_CLAIM_RE = re.compile(
    r"\btests? (?:pass|passed|are green|all green|run clean)\b|\b\d+ tests? passed\b"
    r"|\ball tests pass\b|kiểm thử[^.]* (?:đạt|pass)", re.I)
DEPLOY_CLAIM_RE = re.compile(
    r"\b(?:deployed|is live|in production|published)\b|đã (?:deploy|lên sóng)", re.I)
VISUAL_CLAIM_RE = re.compile(r"\b(?:screenshot|screen shot)\b|ảnh chụp", re.I)
TEST_CMD_RE = re.compile(
    r"\b(?:pytest|jest|vitest|npm (?:run )?test|yarn test|make test|tox|go test|cargo test)\b", re.I)
EXIT_OK_RE = re.compile(r"exit[=: ]+0\b")
URL_RE = re.compile(r"https?://[^\s)\]]+")
# Deploy local không có URL: receipt phải là quan sát target state (ls/stat/hash/diff).
STATE_CHECK_RE = re.compile(r"\b(?:ls|stat|shasum|sha\d+sum|md5|diff|cmp)\b", re.I)
EXIT_LINE_RE = re.compile(r"^\s*exit[=: ]+\d+\s*$", re.I)


def _cmd_text(call: Dict[str, Any]) -> str:
    args = call.get("args") or call.get("arguments") or {}
    if isinstance(args, dict):
        for key in ("command", "Command", "cmd", "Cmd"):
            if isinstance(args.get(key), str):
                return args[key]
    return str(args)


# echo/printf/interpreter one-liner chỉ phát lại chữ trong lệnh: tự chứng,
# không bao giờ là receipt.
ECHO_RE = re.compile(
    r"^\s*(?:echo|printf|python3?\s+(?:-c|-m\s+\w+\s+-c)|node\s+-e|ruby\s+-e|perl\s+-e)\b",
    re.I)


def _call_output_pairs(steps: List[Dict[str, Any]]) -> Iterator[Tuple[Dict[str, Any], str]]:
    outputs = {}
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        oid = step.get("tool_call_id") or step.get("call_id")
        if oid:
            outputs[str(oid)] = str(step.get("content") or "")
    for step in steps or []:
        calls = step.get("tool_calls") if isinstance(step, dict) else None
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict) and not ECHO_RE.match(_cmd_text(call)):
                yield call, outputs.get(str(call.get("id") or call.get("tool_call_id") or ""), "")


def _target_state_observed(pairs) -> bool:
    """State-check command với output thật (không chỉ exit line, không phải
    output của chính script deploy)."""
    for call, out in pairs:
        cmd = str(call.get("args") or call.get("arguments") or "")
        if not STATE_CHECK_RE.search(cmd):
            continue
        observed = [l for l in str(out or "").splitlines()
                    if l.strip() and not EXIT_LINE_RE.match(l)]
        if observed:
            return True
    return False


def uncovered_claims(reply: str, steps: List[Dict[str, Any]]) -> List[str]:
    """Gaps between what the reply claims and what receipts of that kind prove."""
    text = str(reply or "")
    pairs = list(_call_output_pairs(steps))
    receipt_text = "\n".join(
        f"{call.get('args') or call.get('arguments') or ''} {out}" for call, out in pairs)
    gaps: List[str] = []
    if TEST_CLAIM_RE.search(text):
        covered = any(TEST_CMD_RE.search(str(call.get("args") or call.get("arguments") or ""))
                      and EXIT_OK_RE.search(out) for call, out in pairs)
        if not covered:
            gaps.append("test claim: no test-run receipt with exit=0")
    if DEPLOY_CLAIM_RE.search(text) and not URL_RE.search(receipt_text) \
            and not _target_state_observed(pairs):
        gaps.append("deploy claim: no target-state receipt "
                    "(URL/status check, or ls/stat/hash of the deployed target)")
    if VISUAL_CLAIM_RE.search(text):
        from sage.imgtext import visual_text_and_flags
        visual, flags = visual_text_and_flags(steps)
        if not visual:
            gaps.append("visual claim: no screenshot receipt")
        elif flags:
            gaps.append(f"visual claim: screenshot shows {', '.join(flags)}")
    return gaps


def claim_contract_hint(reply: str, steps: List[Dict[str, Any]]) -> Optional[str]:
    """not_verified steer shaped like the Compass hint; None when covered or unaudited."""
    if not steps:
        return None
    gaps = uncovered_claims(reply, steps)
    if not gaps:
        return None
    criterion = (COMPASS_CATEGORIES.get("not_verified") or {}).get(
        "criterion", "A material outcome lacks evidence")
    return f"jev_compass not_verified: {criterion} Evidence: " + "; ".join(gaps)


def claim_contract_hint_for(reply: str, transcript_path: str) -> Optional[str]:
    """Hook entry: same hint, steps read from a transcript path."""
    from sage.transcript import _read_transcript_steps
    try:
        steps = _read_transcript_steps(transcript_path)
    except Exception:
        return None
    return claim_contract_hint(reply, steps)
