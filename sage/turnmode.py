"""
sage.turnmode - Turn-shape flags: work vs normal-qa vs casual-qa.

Verdict routing must never call a turn "undone" when nothing was ordered:
normal-qa (question) and casual-qa (narrative, chit-chat) carry no
deliverable. Any work order keeps the session auditable, so the strongest
flag across user turns wins.
"""
import re
from typing import Any, Dict, List

WORK = "work"
NORMAL_QA = "normal-qa"
CASUAL_QA = "casual-qa"

# Hit anywhere: a false "work" hit keeps checking (safe), a missed one does not.
_WORK_RE = re.compile(
    r"\b(?:please\s+)?(?:fix|add|build|run|write|deploy|ship|rename|update|create|"
    r"edit|refactor|commit|push|install|configure|implement|remove|delete|extract|"
    r"compress|migrate|patch|wire|deploy|verify|stop|make|set\s+up|clean|rewrite|"
    r"tweak|nén|viết|sửa|thêm|gỡ|chạy|cài|đổi|xóa|tạo|cắt|triển khai|commit)\b",
    re.I,
)
_QUESTION_START_RE = re.compile(
    r"^\W*(?:what|why|how|where|when|which|who|whose|can|could|should|would|is|are|"
    r"does|do|tại sao|tai sao|sao|cái gì|cai gi|ở đâu|o dau|khi nào|khi nao|"
    r"như thế nào|nhu the nao|bao nhiêu|bao nhieu|có cách|co cach|ai)\b",
    re.I,
)


def turn_mode(text: str) -> str:
    """Flag one message: work order, question, or conversational narrative."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return CASUAL_QA
    if _WORK_RE.search(cleaned):
        return WORK
    if "?" in cleaned or _QUESTION_START_RE.match(cleaned):
        return NORMAL_QA
    return CASUAL_QA


def session_mode(steps: List[Dict[str, Any]]) -> str:
    """Strongest flag across real user turns: work beats normal-qa beats casual-qa."""
    from sage.user_context import is_real_user_step
    modes = {turn_mode(str(s.get("content") or "")) for s in steps or []
             if isinstance(s, dict) and is_real_user_step(s)}
    if WORK in modes:
        return WORK
    if NORMAL_QA in modes:
        return NORMAL_QA
    return CASUAL_QA
