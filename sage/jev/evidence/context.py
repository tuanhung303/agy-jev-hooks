"""Bounded user requirements for the shadow Compass classifier."""
from typing import Any, Dict, List, Optional

from sage.jev.evidence.redact import _RedactBudget, _redact_field
from sage.jev.transport import _bound
from sage.user_context import is_real_user_step, is_trivial_acknowledgment

SHORT_CONTEXT_CHARS = 2200


def _collect_requirements(steps: List[Dict[str, Any]], budget: Optional[_RedactBudget]) -> str:
    """Keep short instructions in arrival order, separately from inert acks."""
    substantive: List[str] = []
    short: List[str] = []
    acks: List[str] = []
    short_chars = omitted = 0
    for step in steps:
        if not is_real_user_step(step):
            continue
        text = str(step.get("content") or "").strip()
        if not text:
            continue
        words = len(text.split())
        # Multiword authorizations also match the shared ack recognizer. Keep
        # them verbatim rather than guessing intent from a vocabulary list.
        if words < 2 and is_trivial_acknowledgment(text):
            acks.append(_bound(_redact_field(text, "ack", budget), 40))
            acks = acks[-8:]
        elif words < 5:
            quote = _bound(_redact_field(text, "short user message", budget), 1500)
            if not omitted and short_chars + len(quote) + 3 <= SHORT_CONTEXT_CHARS:
                short.append(quote)
                short_chars += len(quote) + 3
            else:
                omitted += 1
        else:
            substantive.append(_bound(_redact_field(text, "user message", budget), 1500))
            substantive = substantive[-4:]
    parts = []
    if short:
        parts.append("[short user messages: " + " / ".join(short) + "]")
    if omitted:
        parts.append(f"[later short user messages omitted: {omitted}]")
    if substantive:
        parts.append(_bound(" / ".join(substantive), 3100))
    if not parts:
        parts.append("<none captured>")
    if acks:
        parts.append("[user acks: " + _bound(", ".join(acks), 300) + "]")
    return " ".join(parts)
