#!/usr/bin/env python3
"""zcode-stop-audit.py - Compass label-driven stop gate for ZCode Stop events.

Same gate chain and steering contract as qoder-stop-audit.py: claim contract,
Jev Compass hard labels, stop-phase skill pack, casual restyle. Differences
are the transcript side only: ZCode session history lives in a rollout file
(~/.zcode/cli/rollout/model-io-sess_<session>.jsonl) whose lines are full
model I/O snapshots, so the hook replays the last snapshot's request.messages
plus its final response into the sage step schema (USER_INPUT,
PLANNER_RESPONSE with tool_calls, TOOL_OUTPUT with tool_call_id and isError)
and hands that normalized JSONL to the gates. Clean exits 0 silently; a steer
writes "[zcode-stop-audit] <action>" to stderr and exits 2, capped per
session. Fails open on every error: a missing rollout, an unparseable
snapshot, or a gate exception all pass the stop.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
STATE_DIR = Path("/tmp/zcode_stop_audit")
LOG_PATH = STATE_DIR / "audit.log"
ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"
MAX_STEERS_PER_SESSION = 2
SNIPPET_LIMIT = 1200
# Wall-clock budget for the Compass pre-gate; nothing else runs after it.
JEV_GATE_BUDGET_SECONDS = 8.0
# Cap the injected steer text so ZCode never receives an unbounded message.
STEER_TEXT_LIMIT = 1200
# Singleton skill content needs room; still bounded.
SKILL_TEXT_LIMIT = 3000
MAX_STEPS = 400


def clamp_steer(hint, limit=STEER_TEXT_LIMIT):
    """Clamp steer text to limit, preserving word boundaries when truncated."""
    if not hint or len(hint) <= limit:
        return hint or ""
    truncated = hint[:limit - 3]
    last_space = truncated.rfind(" ")
    if last_space > limit // 2:
        return truncated[:last_space] + "..."
    return hint[:limit]


def _ensure_sage_path():
    """Put the sage source root on sys.path before any sage import."""
    for candidate in (REPO_DIR, Path.home() / ".config" / "agy"):
        if (candidate / "sage" / "jev" / "verdict" / "compass.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    return None


_ensure_sage_path()
try:
    from sage.casual import casual_restyle_hint
except Exception:
    def casual_restyle_hint(reply):
        return None

try:
    from sage.skillpack import stop_skill_steer
except Exception:
    def stop_skill_steer(prompt, reply):
        return None

try:
    from sage.claims import claim_contract_hint_for
except Exception:
    def claim_contract_hint_for(reply, transcript_path):
        return None

try:
    from sage.sanitizer import redact_secrets as _redact_secrets
except Exception:
    _redact_secrets = None


def emit_steer(hint, limit=STEER_TEXT_LIMIT):
    """Sanitize once, then bound: logging, stderr and the caller get one string.

    A missing or failing sanitizer is a silent exit, never an unsanitized steer.
    """
    if _redact_secrets is None:
        return None
    try:
        safe = _redact_secrets(hint)
    except Exception:
        return None
    return clamp_steer(safe, limit)


def log(message):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


def resolve_rollout(payload):
    """ZCode rollout file for this session: explicit path, then rollout dir."""
    explicit = payload.get("transcript_path") or payload.get("transcriptPath")
    if explicit and os.path.exists(str(explicit)):
        return Path(explicit)
    session_id = str(payload.get("session_id") or payload.get("sessionId")
                     or payload.get("conversationId") or "")
    if not session_id:
        return None
    bare = session_id[5:] if session_id.startswith("sess_") else session_id
    for name in (f"model-io-sess_{session_id}.jsonl", f"model-io-sess_{bare}.jsonl"):
        candidate = ROLLOUT_DIR / name
        if candidate.is_file():
            return candidate
    return None


def _message_text(content):
    """Text of a user/assistant content: plain string or typed part list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(p.get("text") or "") for p in content
                 if isinstance(p, dict) and p.get("type") in ("text", "output_text")]
        return "\n".join(part for part in parts if part.strip())
    return ""


def _calls_of(raw_calls):
    calls = []
    for call in raw_calls if isinstance(raw_calls, list) else []:
        if not isinstance(call, dict) or not call.get("name"):
            continue
        calls.append({
            "name": call.get("name"),
            "id": call.get("id") or "",
            "args": call.get("input") if isinstance(call.get("input"), dict) else {},
        })
    return calls


def steps_from_rollout(rollout_path):
    """Replay the newest model I/O snapshot into sage transcript steps.

    The last line holds the finished turn: request.messages is the full
    conversation before the final reply and response carries that reply.
    Message timestamps are unknown, so steps get increasing synthetic times
    walking back from the snapshot's startedAt.
    """
    size = rollout_path.stat().st_size
    with rollout_path.open("rb") as handle:
        if size > 16 * 1024 * 1024:
            handle.seek(-2 * 1024 * 1024, 2)
            handle.readline()  # drop the partial line
        lines = handle.read().decode("utf-8", errors="replace").splitlines()
    snapshot = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if candidate.get("type") == "model_io" and isinstance(candidate.get("request"), dict):
            snapshot = candidate
            break
    if snapshot is None:
        return None, None, None
    response = snapshot.get("response") or {}
    if response.get("finishReason") not in (None, "stop"):
        return None, None, None
    messages = (snapshot.get("request") or {}).get("messages")
    if not isinstance(messages, list) or not messages:
        return None, None, None

    started_at = str(snapshot.get("startedAt") or "")
    base = None
    try:
        base = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        base = None
    def ts(index):
        if base is None:
            return None
        return (base + timedelta(seconds=index)).isoformat()

    steps = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        stamp = ts(index)
        if role == "user":
            text = _message_text(message.get("content"))
            if text.strip():
                steps.append({"type": "USER_INPUT", "content": text,
                              "created_at": stamp, "source": ""})
        elif role == "assistant":
            calls = _calls_of(message.get("toolCalls"))
            text = _message_text(message.get("content"))
            if text.strip() or calls:
                steps.append({"type": "PLANNER_RESPONSE", "content": text,
                              "tool_calls": calls, "created_at": stamp})
        elif role == "tool":
            content = message.get("content")
            steps.append({
                "type": "TOOL_OUTPUT",
                "content": content if isinstance(content, str) else _message_text(content),
                "tool_call_id": str(message.get("toolCallId") or ""),
                "isError": message.get("isError") is True,
                "created_at": stamp,
            })
    reply = str(response.get("text") or "")
    tail_calls = _calls_of(response.get("toolCalls"))
    if reply.strip() or tail_calls:
        steps.append({"type": "PLANNER_RESPONSE", "content": reply,
                      "tool_calls": tail_calls, "created_at": ts(len(steps))})
    if not steps:
        return None, None, None
    return steps[-MAX_STEPS:], _message_text(reply), started_at


def last_turn_snippets(steps, reply):
    """Prompt from the last USER_INPUT; reply stays whole for style detection."""
    prompt = ""
    for step in steps:
        if step.get("type") == "USER_INPUT" and str(step.get("content") or "").strip():
            prompt = str(step["content"]).strip()
    if not reply.strip() or not prompt:
        return None
    return prompt[-SNIPPET_LIMIT:], reply


def jev_verifier_hint(prompt, reply, transcript_path=""):
    """Return Compass's steer text when a hard label fires, else None.

    prompt/reply stay in the signature for the Hermes gate's positional call;
    Compass judges the assembled transcript evidence, not the bare pair.
    Any error fails open to None so the stop is never blocked by the gate.
    """
    if os.environ.get("ZCODE_JEV_GATE") == "0":
        return None
    candidates = (REPO_DIR, Path.home() / ".config" / "agy")
    try:
        for candidate in candidates:
            if (candidate / "sage" / "jev" / "verdict" / "compass.py").is_file():
                if str(candidate) not in sys.path:
                    sys.path.insert(0, str(candidate))
                from sage.jev.verdict.compass import jev_compass_hint
                started = time.monotonic()
                hint = jev_compass_hint(transcript_path,
                                        deadline=time.monotonic() + JEV_GATE_BUDGET_SECONDS)
                if hint:
                    log(f"jev_compass steer ({time.monotonic() - started:.2f}s)")
                return hint
    except Exception as exc:
        log(f"jev compass unavailable: {exc}")
    return None


def steer_count(session_id):
    counter = STATE_DIR / f"{session_id}.steers"
    try:
        return int(counter.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def bump_steer_count(session_id):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"{session_id}.steers").write_text(str(steer_count(session_id) + 1))
    except OSError:
        pass


def main():
    if os.environ.get("ZCODE_SAGE_DISABLED") == "1" or os.environ.get("ZCODE_STOP_AUDIT") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("stop_hook_active"):
        return 0

    session_id = str(payload.get("session_id") or payload.get("sessionId")
                     or payload.get("conversationId") or "")
    if not session_id:
        log(f"payload missing session id: {sorted(payload)[:8]}")
        return 0

    if steer_count(session_id) >= MAX_STEERS_PER_SESSION:
        return 0

    rollout = resolve_rollout(payload)
    if rollout is None:
        log(f"no rollout file for session={session_id}")
        return 0
    try:
        steps, reply, started_at = steps_from_rollout(rollout)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        log(f"rollout unreadable session={session_id}: {type(exc).__name__}")
        return 0
    if not steps:
        log(f"no finished turn in rollout session={session_id} started={started_at or 'none'}")
        return 0

    snippets = last_turn_snippets(steps, reply)
    if snippets is None:
        return 0
    prompt, reply = snippets

    steps_path = STATE_DIR / f"{session_id}.steps.jsonl"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with steps_path.open("w", encoding="utf-8") as handle:
            for step in steps:
                handle.write(json.dumps(step, ensure_ascii=False) + "\n")
    except OSError:
        steps_path = None
    if steps_path is None:
        return 0

    # Claim contract is local and deterministic: the Jev kill switch cuts only
    # the remote compass call, never this stage.
    hint = claim_contract_hint_for(reply, str(steps_path))
    tag, limit = "CLAIM", STEER_TEXT_LIMIT
    if not hint:
        hint = jev_verifier_hint(prompt, reply, str(steps_path))
        tag, limit = "FAIL", STEER_TEXT_LIMIT
    if not hint:
        hint = stop_skill_steer(prompt, reply)
        tag, limit = "SKILL", SKILL_TEXT_LIMIT
    if not hint:
        hint = casual_restyle_hint(reply)
        tag, limit = "CASUAL", STEER_TEXT_LIMIT
    if hint:
        action = emit_steer(hint, limit)
        if not action:
            log(f"{tag} suppressed: sanitizer unavailable or failed")
            return 0
        bump_steer_count(session_id)
        log(f"{tag} session={session_id}: {action}")
        sys.stderr.write(f"[zcode-stop-audit] {action}\n")
        return 2
    log(f"PASS session={session_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"unhandled error: {exc}")
        sys.exit(0)
