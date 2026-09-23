"""sage.jev.evidence.attribution - Call/result binding and status extraction.

Binds tool calls to their result steps by identity (global tool_call_id map,
then 1-to-1 positional fallback) and reads a structured exit status. Identity
wins over position: parallel calls must never share receipts.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

EXIT_RE = re.compile(r"(?:exit[=: ]+|exited with code )(\d+)", re.I)
_RECEIPT_START_RE = re.compile(
    r"^(?:Created At:.*?\n(?:Completed At:.*?\n)?\s*)?(?:The command exited with code \d+|exit[=: ]+\d+)",
    re.I | re.S,
)

_NON_RESULT_TYPES = {
    "USER_INPUT", "CHECKPOINT", "SUMMARY", "CONVERSATION_SUMMARY",
    "COMPACTION", "SYSTEM_SUMMARY", "PLANNER_RESPONSE",
}


def _first_str(mapping: Dict[str, Any], keys: tuple) -> Optional[str]:
    """First present string value; distinguishes cleared (empty) from absent (None)."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            return value
    return None


def _step_tools(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls = step.get("tool_calls")
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def _result_id(step: Dict[str, Any]) -> str:
    """Result steps key only by explicit tool-result references, never a generic id."""
    oid = step.get("tool_call_id") or step.get("call_id")
    if not oid and isinstance(step.get("metadata"), dict):
        oid = step["metadata"].get("tool_call_id") or step["metadata"].get("call_id")
    return str(oid) if oid else ""


def _is_result_step(step: Dict[str, Any]) -> bool:
    """Exclude known non-result records instead of whitelisting: real AGY
    receipts ride in GENERIC steps, while checkpoints and summaries must not
    become receipts."""
    return str(step.get("type") or "").upper() not in _NON_RESULT_TYPES


def _status_of(step: Dict[str, Any], raw: str) -> str:
    """Explicit tool failure overrides numeric status and textual receipts."""
    sources = (step, step.get("metadata") if isinstance(step.get("metadata"), dict) else {})
    if any(s.get(key) is True for s in sources for key in ("isError", "is_error")):
        return "error"
    for source in sources:
        for key in ("exit_code", "returncode", "ExitCode", "exitCode"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
    match = EXIT_RE.search(raw or "")
    return match.group(1) if match else "unknown"


def _bind_result_step(
    call: Dict[str, Any], t_idx: int, calls: List[Dict[str, Any]],
    window_results: List[Dict[str, Any]], output_ids: Dict[str, Dict[str, Any]],
    duplicate_ids: set,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
    """Resolve the result step for a call: global ID map, then 1-to-1 positional.

    Returns (result_step, raw_output_or_None, ambiguous)."""
    cid = str(call.get("id") or call.get("tool_call_id") or call.get("call_id") or "")
    if cid and cid in output_ids and cid not in duplicate_ids:
        step = output_ids[cid]
        raw = str(step.get("content") or "")
        return step, raw, False
    has_ids = any(c.get("id") or c.get("tool_call_id") or c.get("call_id") for c in calls)
    if not has_ids and not any(_result_id(s) for s in window_results):
        if len(calls) == len(window_results):
            step = window_results[t_idx]
            raw = str(step.get("content") or "")
            # An ID-less output must be receipt-shaped (structured status
            # flags or a leading exit code), never prose that merely
            # mentions an exit code.
            shaped = _status_of(step, "") != "unknown" or bool(_RECEIPT_START_RE.match(raw))
            if shaped:
                return step, raw, False
    return None, None, True
