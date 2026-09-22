"""sage.jev.evidence.redact - Bounded secret redaction for Compass evidence.

redact_secrets is quadratic on input size, so fields larger than
SAFE_REDACT_LIMIT are omitted instead of truncated (a sliced secret can
survive redaction), and a cumulative wall-clock budget bounds worst cases.
"""
import time
from typing import Any, Optional

from sage.sanitizer import redact_secrets

SAFE_REDACT_LIMIT = 8000
REDACT_TIME_BUDGET_S = 5.0  # cumulative cap within the 10-second hook target


class _RedactBudget:
    """Cumulative wall-clock budget: redact_secrets is quadratic on size, so
    worst-case adversarial fields must stay bounded for hook latency."""

    def __init__(self, seconds: float):
        self.remaining = seconds

    def spend(self, started: float) -> None:
        self.remaining -= time.monotonic() - started

    def exhausted(self) -> bool:
        return self.remaining <= 0.0


def _redact_field(text: Any, label: str, budget: Optional[_RedactBudget] = None) -> str:
    """Redact a complete field; oversized or unaffordable fields are omitted."""
    if text is None:
        return ""
    text = str(text)
    if len(text) > SAFE_REDACT_LIMIT:
        return f"[{label} omitted: {len(text)} chars exceed safe redaction size]"
    if budget is not None:
        if budget.exhausted():
            return f"[{label} omitted: redaction budget exhausted]"
        started = time.monotonic()
        try:
            return redact_secrets(text)
        finally:
            budget.spend(started)
    return redact_secrets(text)
