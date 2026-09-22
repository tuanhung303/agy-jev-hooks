"""
sage.casual - Casual category: robotic or verbose reply detection.

Style-only check for the stop gate. It never questions the work, only how
the reply reads; the steer points at the bro skill so the agent restates the
same answer instead of redoing it.
"""
import re
from typing import Optional

RESTYLE_HINT = (
    "[@bro](skill://bro) restyle: your reply reads robotic or verbose. "
    "Restate the same answer plainly and concisely, human to human. "
    "Do not redo any work."
)

# Template tells: openings and closers no human drafts.
_ROBOTIC_PHRASES = (
    "certainly!", "great question", "sure thing", "happy to help",
    "i hope this helps", "hope this helps", "feel free to", "as an ai",
    "in conclusion", "in summary", "to summarize", "it's worth noting",
    "please note that",
)
# Marketing register; three or more is a pileup.
_HYPE_WORDS = {
    "absolutely", "incredibly", "crucial", "robust", "seamless", "comprehensive",
    "leverage", "leveraging", "delve", "elevate", "streamline", "cutting-edge",
    "game-changer", "supercharge", "amazing", "fantastic",
}
HYPE_WORD_LIMIT = 3
BREVITY_WORD_LIMIT = 220
BULLET_LIMIT = 8

_WORD_RE = re.compile(r"[a-zA-Z']+")
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+", re.M)
# Code, paths, receipts: evidence records, not prose. Never restyle those.
_TECH_LINE_RE = re.compile(
    r"^\s*(?:```|\$ |[-+]\s|#{1,6}\s)|[/\\][\w.-]+[/\\]|"
    r"[\w./-]+\.(?:py|ts|tsx|js|md|ya?ml|json|toml|sh|txt|lock)\b|\b\w+\([^)]*\)",
    re.I,
)
EVIDENCE_LINE_RATIO = 0.5


def casual_restyle_hint(reply: Optional[str]) -> Optional[str]:
    """Restyle steer when the reply reads like a template or runs long; else None."""
    text = str(reply or "").strip()
    if not text:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and sum(1 for line in lines if _TECH_LINE_RE.search(line)) / len(lines) >= EVIDENCE_LINE_RATIO:
        return None
    if any(p in text.lower() for p in _ROBOTIC_PHRASES):
        return RESTYLE_HINT
    words = _WORD_RE.findall(text.lower())
    if len(words) > BREVITY_WORD_LIMIT:
        return RESTYLE_HINT
    if sum(1 for w in words if w in _HYPE_WORDS) >= HYPE_WORD_LIMIT:
        return RESTYLE_HINT
    if len(_BULLET_RE.findall(text)) >= BULLET_LIMIT and len(words) > 120:
        return RESTYLE_HINT
    return None
