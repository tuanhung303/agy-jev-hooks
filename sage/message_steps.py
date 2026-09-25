"""
sage.message_steps - Normalization of harness message records into step dicts.

Qoder-style transcripts nest tool_use/tool_result inside `message` records;
left raw, tool activity is invisible to evidence assembly and verdict routing
judges empty evidence. Unknown record shapes pass through unchanged.
"""
from typing import Any, Dict, List

MAX_IMAGES_PER_MESSAGE = 3


def _message_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str) and content:
        return [{"type": "text", "text": content}]
    return []


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return ""


def normalize_message_step(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map one role/content-block record onto USER_INPUT / PLANNER_RESPONSE / TOOL_OUTPUT steps."""
    msg = step.get("message")
    if not isinstance(msg, dict):
        return [step]
    role = str(msg.get("role") or "").lower()
    if role not in ("user", "assistant"):
        return [step]
    ts = step.get("created_at") or step.get("timestamp")
    # Claude writes skill bodies and attachment captions as isMeta user
    # records, and task notifications with a non-human origin.kind: neither is
    # a user turn boundary.
    meta = step.get("isMeta") is True
    origin = step.get("origin")
    injected = isinstance(origin, dict) and bool(origin.get("kind")) and origin.get("kind") != "human"
    calls: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    texts: List[str] = []
    images: List[str] = []
    for block in _message_blocks(msg.get("content")):
        kind = str(block.get("type") or "")
        if kind == "tool_use":
            calls.append({
                "name": block.get("name"),
                "id": block.get("id") or block.get("tool_call_id"),
                "args": block.get("input") or block.get("args") or {},
            })
        elif kind == "tool_result":
            result = {
                "type": "TOOL_OUTPUT",
                "content": _result_text(block.get("content")),
                "tool_call_id": block.get("tool_use_id") or block.get("id") or "",
                "created_at": ts,
            }
            if block.get("is_error") is True:
                result["is_error"] = True
            results.append(result)
        elif kind == "image":
            source = block.get("source") or {}
            data = source.get("data") if isinstance(source, dict) else None
            if isinstance(data, str) and data and len(images) < MAX_IMAGES_PER_MESSAGE:
                images.append(data)
        elif kind in ("text", "output_text") and not (meta and role == "user"):
            text = str(block.get("text") or "").strip()
            if text:
                texts.append(text)
    out = results
    if role == "assistant":
        if texts or calls or images:
            step_out = {"type": "PLANNER_RESPONSE", "content": "\n".join(texts),
                        "tool_calls": calls, "created_at": ts}
            if images:
                step_out["images"] = images
            out.append(step_out)
    elif texts:
        step_out = {"type": "USER_INPUT", "content": "\n".join(texts), "created_at": ts}
        if injected:
            step_out["source"] = "SYSTEM"
            step_out["origin"] = origin
        if images:
            step_out["images"] = images
        out.append(step_out)
    elif images:
        out.append({"type": "CHECKPOINT", "content": "", "images": images, "created_at": ts})
    return out
