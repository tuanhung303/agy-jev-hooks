#!/usr/bin/env python3
"""hermes-stop-review.py - Jev/Compass review gate for Hermes stop events.

Two phases, one script (registered on two shell-hook events):

  pre_llm_call : cache the user prompt and conversation history (bounded,
                 AGY-step jsonl) under /tmp/hermes_stop_review/<session>.*.
                 Always emits {} — this phase never injects context.
  pre_verify   : runs the shared gate (qoder-stop-audit.py's
                 jev_verifier_hint: Compass label-driven steer, hard label
                 above floor only, no suspicion fallback) on the draft final
                 response. A steer emits
                 {"decision": "block", "reason": steer} so Hermes routes the
                 steer back to the main agent; no suspicion emits {} and the
                 turn finishes. Fails open on every error; at most
                 MAX_STEERS_PER_SESSION blocks per session.

Hermes carries no transcript_path on pre_verify, so the gate consumes the
history cached at pre_llm_call as its transcript. pre_verify only fires on
turns that edited code, so chat-only turns are never gated.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
STATE_DIR = Path("/tmp/hermes_stop_review")
LOG_PATH = STATE_DIR / "audit.log"
MAX_STEERS_PER_SESSION = 2
MAX_ATTEMPT = 3                 # pre_verify attempt cap (loop guard)
MAX_PROMPT_CHARS = 4000
MAX_HISTORY_CHARS = 60000       # tail budget for the cached transcript
STEER_TEXT_LIMIT = 1200
GATE_SCRIPT = HOOK_DIR / "qoder-stop-audit.py"


def clamp_steer(hint, limit=STEER_TEXT_LIMIT):
    """Clamp steer text to limit, preserving word boundaries when truncated."""
    if not hint or len(hint) <= limit:
        return hint or ""
    truncated = hint[:limit - 3]
    last_space = truncated.rfind(" ")
    if last_space > limit // 2:
        return truncated[:last_space] + "..."
    return hint[:limit]


def log(message):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


def _as_text(value):
    """User/assistant payloads may be str or content-part list."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _load_gate():
    """Import the shared jev_verifier_hint gate from qoder-stop-audit.py."""
    spec = importlib.util.spec_from_file_location("qoder_stop_audit_gate", GATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load gate spec from {GATE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_session(session_id):
    return "".join(c for c in str(session_id) if c.isalnum() or c in "-_.") or "default"


def _session_paths(session_id):
    safe = _safe_session(session_id)
    return (STATE_DIR / f"{safe}.prompt", STATE_DIR / f"{safe}.steps.jsonl")


def _steer_file(session_id):
    return STATE_DIR / f"{_safe_session(session_id)}.steers"


def _history_steps(history):
    """Map Hermes conversation_history entries to AGY transcript steps."""
    steps = []
    if not isinstance(history, list):
        return steps
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or entry.get("type") or "").lower()
        text = _as_text(entry.get("content") or entry.get("text")).strip()
        if not text:
            continue
        if role in ("user", "human", "user_input"):
            steps.append({"type": "USER_INPUT", "content": text})
        elif role in ("assistant", "ai", "model", "planner_response"):
            steps.append({"type": "PLANNER_RESPONSE", "content": text, "tool_calls": []})
    return steps


def cache_phase(payload):
    """pre_llm_call: persist prompt + history for the review phase."""
    session_id = payload.get("session_id")
    extra = payload.get("extra")
    if not session_id or not isinstance(extra, dict):
        return {}
    prompt = _as_text(extra.get("user_message")).strip()
    steps = _history_steps(extra.get("conversation_history"))
    if not prompt:
        for step in reversed(steps):
            if step["type"] == "USER_INPUT":
                prompt = step["content"]
                break
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        prompt_file, steps_file = _session_paths(session_id)
        if prompt:
            prompt_file.write_text(prompt[-MAX_PROMPT_CHARS:], encoding="utf-8")
        if steps:
            kept, used = [], 0
            for step in reversed(steps):      # keep the newest tail
                size = len(step.get("content") or "")
                if used + size > MAX_HISTORY_CHARS:
                    break
                kept.append(step)
                used += size
            with steps_file.open("w", encoding="utf-8") as handle:
                for step in reversed(kept):   # restore chronological order
                    handle.write(json.dumps(step, ensure_ascii=False) + "\n")
    except OSError as exc:
        log(f"cache write failed: {exc}")
    return {}


def steer_count(session_id):
    try:
        return int(_steer_file(session_id).read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def bump_steer_count(session_id):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _steer_file(session_id).write_text(str(steer_count(session_id) + 1))
    except OSError:
        pass


def review_phase(payload):
    """pre_verify: block-with-steer on suspicion, silent pass otherwise."""
    if os.environ.get("HERMES_STOP_REVIEW") == "0":
        return {}
    session_id = payload.get("session_id")
    extra = payload.get("extra")
    if not session_id or not isinstance(extra, dict):
        log(f"payload missing fields: {sorted(str(k) for k in payload)[:8]}")
        return {}
    try:
        attempt = int(extra.get("attempt") or 1)
    except (TypeError, ValueError):
        attempt = 1
    if attempt > MAX_ATTEMPT:
        return {}
    if steer_count(session_id) >= MAX_STEERS_PER_SESSION:
        return {}
    reply = _as_text(extra.get("final_response")).strip()
    if not reply:
        return {}
    prompt_file, steps_file = _session_paths(session_id)
    try:
        prompt = prompt_file.read_text(encoding="utf-8").strip()
    except OSError:
        prompt = ""
    transcript_path = ""
    if steps_file.is_file():
        # Review copy = cached history + the draft final reply as the last
        # PLANNER_RESPONSE, so Compass sees the claim it must judge.
        try:
            lines = steps_file.read_text(encoding="utf-8").splitlines()
            lines.append(json.dumps(
                {"type": "PLANNER_RESPONSE", "content": reply, "tool_calls": []},
                ensure_ascii=False,
            ))
            review_file = _steer_file(session_id).with_suffix(".review.jsonl")
            review_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            transcript_path = str(review_file)
        except OSError as exc:
            log(f"review transcript write failed: {exc}")
    try:
        gate = _load_gate()
        hint = gate.jev_verifier_hint(prompt, reply, transcript_path)
    except Exception as exc:
        log(f"gate unavailable: {exc}")
        return {}
    if not hint:
        log(f"PASS session={session_id} attempt={attempt}")
        return {}
    bump_steer_count(session_id)
    steer = clamp_steer(hint, STEER_TEXT_LIMIT)
    log(f"BLOCK session={session_id} attempt={attempt}: {steer[:200]}")
    return {"decision": "block", "reason": steer}


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        print(json.dumps({}))
        return 0
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("hook_event_name") or ""
    try:
        if event == "pre_llm_call":
            out = cache_phase(payload)
        elif event == "pre_verify":
            out = review_phase(payload)
        else:
            out = {}
    except Exception as exc:
        log(f"unhandled error: {exc}")
        out = {}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
