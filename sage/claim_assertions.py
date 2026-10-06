"""Quote, rehearsal, and negation filters shared by claim detectors."""
import re
from typing import List

_META_CONTEXT_RE = re.compile(
    r"\b(?:fixture|synthetic|mock(?:ed|up)?|placeholder|sample|storyboard|script|narration|narrator|"
    r"frame|frames|beat|beats|scene|shot|slide|video|caption|animation|preview|illustrative|"
    r"demo|kịch bản|ví dụ|hư cấu|giả lập|minh họa)\b", re.I)
_DESCRIPTIVE_RE = re.compile(r"→.*→|\b\d+:\d+\s*[-–]\s*\d+:\d+|\bbeat\s*\d|\bhold\s*\d")
_REPORTED_SPEECH_RE = re.compile(
    r"\b(?:agent|assistant|model|user)\s+(?:says?|said|claims?|reports?)\b|"
    r"khi\s+\w+\s+nói\b|\bnói\s+rằng\b", re.I)
_NEGATION_BEFORE_RE = re.compile(
    r"\b(?:not|never|cannot|can'?t|won'?t|don'?t|doesn'?t|didn'?t|isn'?t|aren'?t|wasn'?t|weren'?t|"
    r"hasn'?t|haven'?t|hadn'?t|no longer|yet to|instead of|rather than|if|unless|whether|"
    r"suppose|imagine|pretend|would|should|could|might|may|will)\b|"
    r"\bchưa\b|\bkhông\b|\bsẽ\b|\bnếu\b|\bđừng\b|\bgiả sử\b", re.I)
_NEGATION_AFTER_RE = re.compile(
    r"\b(?:may|might|does|do|is|are|was|were|could|would|can|will)\s+not\b|"
    r"\bnot\s+(?:pass|passed|succeed|succeeded|prove|settle|mean|imply|guarantee|show|confirm|establish|cover|count)\b|"
    r"\bkhông\s+(?:chắc|hẳn|đủ|rõ)\b|\bchưa\s+(?:chắc|hẳn|đủ|rõ|có)\b|"
    r"\b(?:if|unless|provided that|assuming)\b", re.I)
_CLAIM_NEGATION_RE = re.compile(
    r"\b(?:not|never|cannot|can't|won't|don't|didn't|if|unless|whether)\b|"
    r"\b(?:chưa|không|nếu|sẽ)\b", re.I)


def _quote_state(text: str, active=None, end=None):
    closing = {'“': '”', '‘': '’'}
    for index, char in enumerate(text[:end]):
        if char == "'" and index and index + 1 < len(text) and text[index - 1].isalnum() and text[index + 1].isalnum():
            continue
        if active:
            if char == active:
                active = None
        elif char in {'"', "'", '`', '“', '‘'}:
            active = closing.get(char, char)
    return active


def _quotes_open_at(text: str, end: int, initial=None) -> bool:
    return _quote_state(text, initial, end) is not None


def _sentences(text: str, initial_quote=None) -> List[str]:
    out = []
    active_quote = initial_quote
    for line in str(text or "").splitlines():
        start = 0
        for index, char in enumerate(line):
            if char in ".!?;" and not _quotes_open_at(line, index + 1, active_quote):
                if index + 1 == len(line) or line[index + 1].isspace():
                    part = line[start:index + 1].strip()
                    if part:
                        out.append(part)
                    start = index + 1
        active_quote = _quote_state(line, active_quote)
        tail = line[start:].strip()
        if tail:
            out.append(tail)
    return out


def _is_assertion(sentence: str, match: re.Match, line: str = "", initial_quote=None) -> bool:
    """False for rehearsal, reported speech, quotes, and hedged phrasing."""
    if _DESCRIPTIVE_RE.search(line or sentence):
        return False
    if _META_CONTEXT_RE.search(sentence) or _REPORTED_SPEECH_RE.search(sentence):
        return False
    start, end = match.span()
    if (_CLAIM_NEGATION_RE.search(match.group(0))
            or _quotes_open_at(sentence, start, initial_quote)):
        return False
    before = sentence[:start]
    after = sentence[end:]
    return not (_NEGATION_BEFORE_RE.search(before) or _NEGATION_AFTER_RE.search(after))


def _claim_sentences(text: str, regex: re.Pattern) -> List[str]:
    out = []
    active_quote = None
    for line in str(text or "").splitlines():
        for sentence in _sentences(line, active_quote):
            match = regex.search(sentence)
            if match and _is_assertion(sentence, match, line, active_quote):
                out.append(sentence.strip())
        active_quote = _quote_state(line, active_quote)
    return out
