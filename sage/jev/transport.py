"""sage.jev.transport - One Jev call helper (TypeSafe Jev, spec v4).

All hook decisions route through this single POST: router choice, compass
booleans, verifier choice. One retry on transient 503s; callers own budgets
via attempt_timeout/deadline and fail open on every error.
"""
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from sage.config import (
    JEV_GATE_API_KEY,
    JEV_GATE_MODEL_ID,
    JEV_GATE_TIMEOUT,
    JEV_GATE_URL,
)

_PROTOCOL_VERSION = "0.0.1"
_SPEC_VERSION = "4"
_MARKER = "\n...[truncated]...\n"


def _finite_unit(value: Any) -> Optional[float]:
    """Accept only real numbers strictly inside [0, 1]; booleans and strings fail."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")) or not 0.0 <= out <= 1.0:
        return None
    return out


def _bound(text: str, limit: int) -> str:
    """Bound a text chunk to at most ``limit`` chars, keeping head and tail."""
    text = str(text or "").strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    head = max(0, (limit - len(_MARKER)) * 2 // 3)
    tail = max(0, limit - len(_MARKER) - head)
    if tail:
        return text[:head] + _MARKER + text[-tail:]
    return text[:head]


def _call_jev(
    state: Dict[str, Any],
    questions: Dict[str, Any],
    attempt_timeout: float,
    deadline: Optional[float],
) -> Dict[str, Any]:
    body = json.dumps({"state": state, "questions": questions, "providerOptions": {}}).encode("utf-8")
    for attempt in range(2):  # one retry: 503s from the gateway are transient
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Jev deadline exhausted")
        req = urllib.request.Request(JEV_GATE_URL, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {JEV_GATE_API_KEY}")
        req.add_header("Content-Type", "application/json")
        req.add_header("ai-gateway-protocol-version", _PROTOCOL_VERSION)
        req.add_header("ai-gateway-auth-method", "api-key")
        req.add_header("ai-evaluation-model-specification-version", _SPEC_VERSION)
        req.add_header("ai-model-id", JEV_GATE_MODEL_ID)
        try:
            with urllib.request.urlopen(req, timeout=attempt_timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            remaining = None if deadline is None else deadline - time.monotonic()
            if exc.code == 503 and attempt == 0 and (remaining is None or remaining > attempt_timeout + 0.5):
                time.sleep(0.5)
                continue
            raise
    raise RuntimeError("unreachable retry loop state")  # pragma: no cover
