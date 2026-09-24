#!/usr/bin/env python3
"""agy-stop-audit.py - Compass label-driven stop gate for Antigravity (AGY) Stop events.

Runs on Antigravity's lifecycle Stop event. Evaluates whether the turn's work
is completed against the shared gate chain: claim contract -> Jev Compass hard
labels -> stop-phase skill pack -> casual restyle.

If an audit issue is detected, emits `{"decision": "continue", "reason": "[agy-stop-audit] <action>"}`
so the Antigravity language_server re-invokes the agent loop with steering context.
Otherwise emits `{}` to permit normal termination.

Safety and Loop Prevention:
1. fullyIdle check: when subagents or background tasks are running (fullyIdle=False),
   the hook exits cleanly without blocking, leaving wakeups to reactive messaging.
2. terminationReason check: only audits normal stops (None, "model_stop", "stop");
   errors, user cancels, or max step limits fail open immediately.
3. steer cap: bounded by MAX_STEERS_PER_SESSION (default 2) per session to prevent token drain.
4. fail-open: missing session, unreadable transcript, parser error, or gate timeout
   all pass cleanly with `{}` and zero exit code.
5. kill switches: AGY_SAGE_DISABLED=1, AGY_STOP_AUDIT=0, AGY_JEV_GATE=0.
"""

import json
import os
import sys
import time
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
STATE_DIR = Path("/tmp/agy_stop_audit")
LOG_PATH = STATE_DIR / "audit.log"
MAX_STEERS_PER_SESSION = 2
SNIPPET_LIMIT = 1200
# Wall-clock budget for the Compass pre-gate; nothing else runs after it.
JEV_GATE_BUDGET_SECONDS = 8.0
# Cap the injected steer text so Antigravity never receives an unbounded message.
STEER_TEXT_LIMIT = 1200
# Singleton skill content needs room; still bounded.
SKILL_TEXT_LIMIT = 3000


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
    """Sanitize once, then bound: logging and the caller get one string.

    A missing or failing sanitizer is a silent exit, never an unsanitized steer.
    """
    if _redact_secrets is None:
        return None
    try:
        safe = _redact_secrets(hint)
    except Exception:
        return None
    return clamp_steer(safe, limit)

try:
    from sage.transcript import _read_transcript_steps, get_transcript_path, is_explicit_user_input
except Exception:
    def _read_transcript_steps(path):
        return []
    def get_transcript_path(payload, conv_id):
        return payload.get("transcriptPath") or payload.get("transcript_path")
    def is_explicit_user_input(step):
        return step.get("type") == "USER_INPUT"


def log(message):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


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


def last_turn_snippets(transcript_path):
    """Extract the last explicit user prompt and the last assistant response."""
    steps = _read_transcript_steps(transcript_path)
    if not steps:
        return None
    prompt = reply = ""
    for step in steps:
        if is_explicit_user_input(step):
            prompt = str(step.get("content") or "").strip()
        elif step.get("type") == "PLANNER_RESPONSE":
            content = str(step.get("content") or "").strip()
            if content and content != "None":
                reply = content
    if not prompt or not reply:
        return None
    return prompt[-SNIPPET_LIMIT:], reply


def jev_verifier_hint(prompt, reply, transcript_path=""):
    """Return Compass's steer text when a hard label fires, else None.

    Fails open to None on any gate error or when AGY_JEV_GATE=0.
    """
    if os.environ.get("AGY_JEV_GATE") == "0":
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


def resolve_transcript_path(payload, session_id):
    tp = payload.get("transcriptPath") or payload.get("transcript_path")
    if tp and os.path.exists(str(tp)):
        return str(tp)
    for base in ("~/.gemini/antigravity-cli/brain", "~/.gemini/antigravity/brain"):
        fb = os.path.expanduser(f"{base}/{session_id}/.system_generated/logs/transcript.jsonl")
        if os.path.exists(fb):
            return fb
    return str(tp) if tp else None


def main():
    if os.environ.get("AGY_SAGE_DISABLED") == "1" or os.environ.get("AGY_STOP_AUDIT") == "0":
        sys.stdout.write(json.dumps({}))
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.stdout.write(json.dumps({}))
        return 0
    if not isinstance(payload, dict):
        sys.stdout.write(json.dumps({}))
        return 0

    # Stop hook re-entrancy guard
    if payload.get("stop_hook_active"):
        sys.stdout.write(json.dumps({}))
        return 0

    # Subagent and background task safety: never block when subagents are active
    if payload.get("fullyIdle") is False:
        log("fullyIdle is False; abstaining to preserve reactive wakeups")
        sys.stdout.write(json.dumps({}))
        return 0

    # Termination reason guard: fail open on errors, user cancels, or max invocations
    term_reason = str(payload.get("terminationReason") or "").upper()
    abort_reasons = {
        "ERROR", "EXECUTOR_TERMINATION_REASON_ERROR",
        "USER_CANCELED", "EXECUTOR_TERMINATION_REASON_USER_CANCELED",
        "MAX_INVOCATIONS", "EXECUTOR_TERMINATION_REASON_MAX_INVOCATIONS",
        "MAX_FORCED_INVOCATIONS", "EXECUTOR_TERMINATION_REASON_MAX_FORCED_INVOCATIONS",
        "MAX_TOKEN_BUDGET_EXCEEDED", "EXECUTOR_TERMINATION_REASON_MAX_TOKEN_BUDGET_EXCEEDED",
        "USER_CANCEL", "ABORTED", "MAX_STEPS_EXCEEDED",
    }
    if term_reason in abort_reasons:
        log(f"abort termination reason: {term_reason}; abstaining")
        sys.stdout.write(json.dumps({}))
        return 0

    session_id = str(
        payload.get("conversationId")
        or payload.get("session_id")
        or payload.get("sessionId")
        or ""
    )
    transcript_path = resolve_transcript_path(payload, session_id)
    if not session_id or not transcript_path:
        log(f"payload missing fields: {sorted(str(k) for k in payload)[:8]}")
        sys.stdout.write(json.dumps({}))
        return 0

    if steer_count(session_id) >= MAX_STEERS_PER_SESSION:
        log(f"steer cap reached for session={session_id}")
        sys.stdout.write(json.dumps({}))
        return 0

    snippets = last_turn_snippets(transcript_path)
    if snippets is None:
        log(f"no snippet pair extracted for session={session_id}")
        sys.stdout.write(json.dumps({}))
        return 0
    prompt, reply = snippets

    # Gate chain:
    # 1. Claim contract: deterministic test/deploy/visual receipt verification
    hint = claim_contract_hint_for(reply, transcript_path)
    tag, limit = "CLAIM", STEER_TEXT_LIMIT

    # 2. Jev Compass classifier: remote hard-label verification
    if not hint:
        hint = jev_verifier_hint(prompt, reply, transcript_path)
        tag, limit = "FAIL", STEER_TEXT_LIMIT

    # 3. Stop-phase skill steer
    if not hint:
        hint = stop_skill_steer(prompt, reply)
        tag, limit = "SKILL", SKILL_TEXT_LIMIT

    # 4. Casual restyle hint
    if not hint:
        hint = casual_restyle_hint(reply)
        tag, limit = "CASUAL", STEER_TEXT_LIMIT

    if hint:
        action = emit_steer(hint, limit)
        if not action:
            log(f"{tag} suppressed: sanitizer unavailable or failed")
            sys.stdout.write(json.dumps({}))
            return 0
        bump_steer_count(session_id)
        log(f"{tag} session={session_id}: {action}")
        sys.stdout.write(json.dumps({
            "decision": "continue",
            "reason": f"[agy-stop-audit] {action}"
        }))
        return 0

    log(f"PASS session={session_id}")
    sys.stdout.write(json.dumps({}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"unhandled error: {exc}")
        sys.stdout.write(json.dumps({}))
        sys.exit(0)
