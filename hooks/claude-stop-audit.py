#!/usr/bin/env python3
"""claude-stop-audit.py - Compass stop gate for Claude Code Stop events.

Shadow by default: each audited stop appends one JSON line to the audit log
and passes. CLAUDE_STOP_AUDIT_MODE=block turns the first fired gate into
exit 2 plus a stderr steer, which Claude feeds back as the next turn.

Gate chain: claim contract, only on turns that edited a file (review and
advice turns quote deploy and test words they do not claim), then Jev Compass
hard labels. The casual restyle and stop-phase skill hints stay off: Claude
loads skills natively, and a restyle re-sends the whole reply.

Loop safety: stop_hook_active skips the continuation of a blocked stop, each
gate blocks at most MAX_STEERS_PER_GATE times per session, and every error
fails open. Kill switches: CLAUDE_STOP_AUDIT=0 (hook), CLAUDE_JEV_GATE=0
(Compass only), plus the shared deploy sentinel.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
# Persistent, not /tmp: the shadow log is the evidence for switching to block.
STATE_DIR = Path(os.environ.get("CLAUDE_STOP_AUDIT_STATE")
                 or Path.home() / ".local" / "state" / "agy-jev-hooks" / "claude-stop-audit")
LOG_PATH = STATE_DIR / "audit.jsonl"
# Deploy sentinel: while it exists every hook no-ops (maintenance procedure
# steps 3-8). Local to the hook so a broken or half-swapped sage can never
# disable the check.
SENTINEL_PATH = Path.home() / ".config" / "agy" / "sage.sentinel"
MAX_STEERS_PER_GATE = 2
# Wall-clock budget for the Compass call; Claude's hook timeout sits above it.
JEV_GATE_BUDGET_SECONDS = 8.0
STEER_TEXT_LIMIT = 1200
PROMPT_LOG_CHARS = 200
STEER_TAG = "[claude-stop-audit]"


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
    from sage.claims import claim_contract_hint
    from sage.jev.evidence.assemble import WRITE_TOOLS, _read_steps_bounded
    from sage.sanitizer import redact_secrets
    from sage.transcript import is_explicit_user_input
except Exception as exc:  # fail open: a broken sage disables the hook
    SAGE_ERROR = f"{type(exc).__name__}: {exc}"
else:
    SAGE_ERROR = None


def log_record(record):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _counter(session_id, gate):
    return STATE_DIR / f"{session_id}.{gate}.steers"


def steer_count(session_id, gate):
    try:
        return int(_counter(session_id, gate).read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def bump_steer_count(session_id, gate):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _counter(session_id, gate).write_text(str(steer_count(session_id, gate) + 1))
    except OSError:
        pass


def sanitize(text, limit=STEER_TEXT_LIMIT):
    """Redact, then bound at a word boundary; None when the sanitizer fails."""
    if not text:
        return None
    try:
        safe = redact_secrets(str(text))
    except Exception:
        return None
    if len(safe) <= limit:
        return safe
    cut = safe[:limit - 3]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut) + "..."


def current_turn(steps):
    """Steps after the last human prompt; the whole list when none is found."""
    start = 0
    for idx, step in enumerate(steps):
        if is_explicit_user_input(step):
            start = idx
    return list(steps[start:])


def turn_prompt(turn):
    if turn and is_explicit_user_input(turn[0]):
        return str(turn[0].get("content") or "").strip()
    return ""


def turn_reply(turn):
    reply = ""
    for step in turn:
        if step.get("type") == "PLANNER_RESPONSE" and str(step.get("content") or "").strip():
            reply = str(step["content"]).strip()
    return reply


def turn_writes(turn):
    return sum(
        1 for step in turn for call in (step.get("tool_calls") or [])
        if isinstance(call, dict) and str(call.get("name") or "").strip().lower() in WRITE_TOOLS
    )


def compass_hint(transcript_path):
    """Compass steer text for a supported hard label, else None (fail open)."""
    if os.environ.get("CLAUDE_JEV_GATE") == "0":
        return None
    try:
        from sage.jev.verdict.compass import jev_compass_hint
        return jev_compass_hint(transcript_path,
                                deadline=time.monotonic() + JEV_GATE_BUDGET_SECONDS)
    except Exception as exc:
        log_record({"ts": _now(), "error": f"compass unavailable: {type(exc).__name__}"})
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(payload, block):
    """Return (decision, gate, steer, record) for one Stop payload."""
    session_id = str(payload.get("session_id") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    started = time.monotonic()
    turn = current_turn(_read_steps_bounded(transcript_path))
    logged_reply = turn_reply(turn)
    reply = str(payload.get("last_assistant_message") or "").strip() or logged_reply
    writes = turn_writes(turn)
    verdicts = {"claim": sanitize(claim_contract_hint(reply, turn)) if writes and reply else None}
    # Shadow mode runs Compass on every stop to collect its verdicts too.
    if not (block and verdicts["claim"]):
        verdicts["compass"] = sanitize(compass_hint(transcript_path))
    decision, gate, steer = "pass", None, None
    for name, text in verdicts.items():
        if not text:
            continue
        if steer_count(session_id, name) >= MAX_STEERS_PER_GATE:
            decision = f"capped:{name}"
            continue
        decision, gate, steer = f"{'block' if block else 'would_block'}:{name}", name, text
        break
    record = {
        "ts": _now(),
        "session": session_id,
        "mode": "block" if block else "shadow",
        "cwd": payload.get("cwd"),
        "transcript": transcript_path,
        "prompt": sanitize(turn_prompt(turn), PROMPT_LOG_CHARS),
        "writes": writes,
        # False means Claude had not flushed the reply when the hook ran, so
        # Compass judged a transcript without it.
        "reply_in_transcript": bool(reply) and logged_reply == reply,
        **verdicts,
        "decision": decision,
        "latency_s": round(time.monotonic() - started, 2),
    }
    return decision, gate, steer, record


def main():
    if os.environ.get("CLAUDE_STOP_AUDIT") == "0" or SENTINEL_PATH.exists():
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    # A continuation of a blocked stop is never re-audited.
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return 0
    transcript_path = str(payload.get("transcript_path") or "")
    if not payload.get("session_id") or not os.path.isfile(transcript_path):
        return 0
    if SAGE_ERROR:
        log_record({"ts": _now(), "error": f"sage unavailable: {SAGE_ERROR}"})
        return 0
    block = os.environ.get("CLAUDE_STOP_AUDIT_MODE") == "block"
    decision, gate, steer, record = audit(payload, block)
    log_record(record)
    if block and steer:
        bump_steer_count(record["session"], gate)
        sys.stderr.write(f"{STEER_TAG} {steer}\n")
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log_record({"ts": _now(), "error": f"unhandled: {type(exc).__name__}: {exc}"})
        sys.exit(0)
