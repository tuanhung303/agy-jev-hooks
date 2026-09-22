"""sage.jev.request.prompt_pair - Last user/agent prompt pair with steering excluded.

Stop hooks inject steering as user-role entries (`[qoder-stop-audit] ...`,
skill suggestions). A raw "last user message" scan would return the steer
instead of the human prompt, so every candidate line is checked against the
exclude_prefixes list in jev.yaml `routing.prompt_pair` before it counts.
"""
from typing import Any, Dict, List, Optional

from sage.jev.config.catalog import load_routing

_USER_TYPES = ("USER_INPUT", "user")
_AGENT_TYPES = ("PLANNER_RESPONSE", "assistant")


def _cfg(routing: Optional[dict]) -> dict:
    data = routing if isinstance(routing, dict) else load_routing()
    cfg = data.get("prompt_pair") or {}
    return {
        "max_chars": int(cfg.get("max_chars", 4000)),
        "exclude_prefixes": tuple(str(p) for p in cfg.get("exclude_prefixes", ())),
    }


def _text(step: Dict[str, Any]) -> str:
    """Step content may be a plain string or a content-part list."""
    content = step.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts).strip()
    message = step.get("message")
    if isinstance(message, dict):
        return _text(message)
    return ""


def _is_steering(text: str, prefixes: tuple) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)


def _is_human(step: Dict[str, Any]) -> bool:
    """Honor origin.kind when present; injected entries often lack it."""
    origin = step.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return origin.get("kind") == "human"
    return True


def extract_prompt_pair(steps: List[Dict[str, Any]], routing: Optional[dict] = None) -> Dict[str, str]:
    """Return bounded {user, agent}: the last human prompt and last reply."""
    cfg = _cfg(routing)
    user = ""
    agent = ""
    for step in steps:
        if not isinstance(step, dict):
            continue
        kind = str(step.get("type") or "")
        text = _text(step)
        if not text or _is_steering(text, cfg["exclude_prefixes"]):
            continue
        if kind in _USER_TYPES and _is_human(step):
            user = text
        elif kind in _AGENT_TYPES:
            agent = text
    bound = cfg["max_chars"]
    return {"user": user[-bound:], "agent": agent[-bound:]}
