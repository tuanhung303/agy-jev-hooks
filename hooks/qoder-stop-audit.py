#!/usr/bin/env python3
"""qoder-stop-audit.py - Compass label-driven stop gate for Qoder Stop events.

Compass decides first: a hard-route label above its floor returns a steer
(text: top label + criterion + one evidence line) and exits 2 so Qoder feeds
it back to the agent; no hard label falls through to the skill pack and the
casual category: a stop-phase skill trigger injects that skill's singleton
content (e.g. TDD), and a robotic or verbose reply returns the
[@bro](skill://bro) restyle steer so the agent restates the same answer
instead of redoing it. Clean exits 0 silently (label-driven verdict, v8
calibration). Fails open on every error and on aborted early-exit
conditions. There is no fork reviewer and no single-category suspicion
fallback: its generic label+score output was the failure mode this replaced.
"""

import json
import os
import sys
import time
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_DIR = HOOK_DIR.parent
STATE_DIR = Path("/tmp/qoder_stop_audit")
LOG_PATH = STATE_DIR / "audit.log"
MAX_STEERS_PER_SESSION = 2
SNIPPET_LIMIT = 1200
# Wall-clock budget for the Compass pre-gate; nothing else runs after it.
JEV_GATE_BUDGET_SECONDS = 8.0
# Cap the injected steer text so Qoder never receives an unbounded message.
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


def jev_verifier_hint(prompt, reply, transcript_path=""):
    """Return Compass's steer text when a hard label fires, else None.

    prompt/reply stay in the signature for the Hermes gate's positional call;
    Compass judges the assembled transcript evidence, not the bare pair.
    Any error fails open to None so the stop is never blocked by the gate.
    """
    if os.environ.get("QODER_JEV_GATE") == "0":
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


def content_text(message):
    parts = message.get("content")
    if not isinstance(parts, list):
        return ""
    return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text").strip()


def last_turn_snippets(transcript_path):
    prompt = reply = ""
    try:
        with open(transcript_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = content_text(entry.get("message") or {})
                if not text:
                    continue
                if entry.get("type") == "assistant":
                    reply = text
                elif entry.get("type") == "user" and (entry.get("origin") or {}).get("kind") == "human":
                    prompt = text
    except OSError:
        return None
    if not reply:
        return None
    # Reply stays whole: style detection judges length and tone, not a tail.
    return prompt[-SNIPPET_LIMIT:], reply


def main():
    if os.environ.get("QODER_SAGE_DISABLED") == "1" or os.environ.get("QODER_STOP_AUDIT") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("stop_hook_active"):
        return 0

    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not session_id or not transcript_path:
        log(f"payload missing fields: {sorted(payload)[:8]}")
        return 0

    if steer_count(session_id) >= MAX_STEERS_PER_SESSION:
        return 0

    snippets = last_turn_snippets(transcript_path)
    if snippets is None:
        return 0
    prompt, reply = snippets

    # Claim contract is local and deterministic: the Jev kill switch cuts only
    # the remote compass call, never this stage.
    hint = claim_contract_hint_for(reply, transcript_path)
    tag, limit = "CLAIM", STEER_TEXT_LIMIT
    if not hint:
        hint = jev_verifier_hint(prompt, reply, transcript_path)
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
        sys.stderr.write(f"[qoder-stop-audit] {action}\n")
        return 2
    log(f"PASS session={session_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"unhandled error: {exc}")
        sys.exit(0)
