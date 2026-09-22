"""Local reference token counter (stand-in for upstream's pinned tiktoken).

UTF-8 bytes / 4 rounded up tracks cl100k closely on code and JSON and keeps the
mandatory report envelope under the smallest supported budget. Every response
declares this counter id, so the accounting basis is never a silent claim.
Never a billing figure.
"""
import math

REFERENCE_COUNTER_ID = "utf8-bytes@1/ceil-4"


def count_reference_tokens(text: str) -> int:
    """Reference tokens of one text; empty text costs nothing."""
    if not text:
        return 0
    return math.ceil(len(text.encode("utf-8")) / 4)


def utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8"))
