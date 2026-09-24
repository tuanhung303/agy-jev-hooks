"""
sage.claims - Claim to receipt contract (layer 3).

A completion claim counts only with a matching receipt: test claims need a
passing test run (exit=0 or a runner pass-count summary) inside the claimed
scope, deploy claims a URL/status receipt for the claimed target, visual-proof
claims a clean screenshot. Only genuine assertions count: quoted rehearsal
(video scripts, narration, diagram descriptions), fixtures, and negated or
conditional statements are not claims. Missing kind is an accurate
not_verified, not a nag. No transcript read: fail open, never guess.
"""
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sage.jev.config.catalog import COMPASS_CATEGORIES

TEST_CLAIM_RE = re.compile(
    r"\btests? (?:pass|passed|are green|all green|run clean)\b|\b\d+ tests? passed\b"
    r"|\ball tests pass\b|kiểm thử[^.]* (?:đạt|pass)", re.I)
DEPLOY_CLAIM_RE = re.compile(
    r"\b(?:deployed|is live|in production|published)\b|đã (?:deploy|lên sóng)", re.I)
VISUAL_CLAIM_RE = re.compile(r"\b(?:screenshot|screen shot)\b|ảnh chụp", re.I)
# Chỉ claim khi reply gắn ảnh với chứng cứ do chính nó tạo, không phải ảnh
# người dùng gửi hay ảnh được nhắc tới trong câu chuyện.
VISUAL_PROOF_RE = re.compile(
    r"\b(?:proof|evidence|attached|verified|chứng minh|đính kèm|dẫn chứng|chụp)\b|"
    r"\.(?:png|jpe?g|webp|gif)\b", re.I)
COMPLETION_CLAIM_RE = re.compile(
    r"\b(?:done|complete[d]?|finished|fixed|implemented|shipped|verified|checked|added|created|"
    r"updated|migrated|xong|hoàn tất|hoàn thành|đã)\b", re.I)
TEST_CMD_RE = re.compile(
    r"\b(?:pytest|unittest|jest|vitest|mocha|rspec|phpunit|node\s+--test|npm (?:run )?test|"
    r"yarn test|make test|tox|go test|cargo test|ctest)\b", re.I)
EXIT_OK_RE = re.compile(r"exit[=: ]+0\b")
URL_RE = re.compile(r"https?://[^\s)\]]+")
PATH_RE = re.compile(
    r"[\w./~-]+\.(?:py|ts|tsx|js|jsx|mjs|json|ya?ml|toml|sh|sql|md|css|html?)\b", re.I)
# Deploy claims need a receipt that checked the target, not any URL that
# happens to appear in the turn's output.
PROBE_CMD_RE = re.compile(r"\b(?:curl|wget|httpie|http|nc|ping|dig|nslookup|openssl)\b", re.I)
# Deploy local không có URL: receipt phải là quan sát target state (ls/stat/hash/diff).
STATE_CHECK_RE = re.compile(r"\b(?:ls|stat|shasum|sha\d+sum|md5|diff|cmp)\b", re.I)
EXIT_LINE_RE = re.compile(r"^\s*exit[=: ]+\d+\s*$", re.I)

# Claims quoted inside a rehearsal (video script, narration, diagram
# description), fixture transcripts, and hedged statements are not this turn's
# claims. A suppressed claim is cheaper than a steer on a non-claim.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
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
    r"\bnot\s+(?:prove|settle|mean|imply|guarantee|show|confirm|establish|cover|count)\b|"
    r"\bkhông\s+(?:chắc|hẳn|đủ|rõ)\b|\bchưa\s+(?:chắc|hẳn|đủ|rõ|có)\b", re.I)

# A runner failure summary outranks any exit token, and a pass-count summary
# counts like exit=0 (piping hides the runner's own status behind the wrapper's).
# The uppercase token stays case-sensitive: runner logs print "failed" freely.
_RUN_FAILURE_RE = re.compile(
    r"\b[1-9]\d*\s+(?:failed|failing|errors?)\b|\bAssertionError\b|\bTraceback\b|"
    r"\berror(?:s)? during collection\b|\bno tests ran\b|#\s*fail\s+[1-9]\d*", re.I)
_RUN_FAILED_TOKEN_RE = re.compile(r"\bFAILED\b")
_RUN_PASS_RE = re.compile(
    r"\b\d+\s+(?:passed|passing|tests? pass(?:ed)?)\b|\btests? passed\b|#\s*pass\s+\d+|"
    r"\b\d+\s*/\s*\d+\b|\[\s*100%\s*\]|\bRan \d+ tests?\b|^OK$", re.I | re.M)


def _sentences(text: str) -> List[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(str(text or "")) if s.strip()]


def _is_assertion(sentence: str, match: re.Match, line: str = "") -> bool:
    """False for rehearsal, reported speech, and hedged or negated phrasing.

    The enclosing line is checked for script shapes (timestamps, node arrows,
    beats) because a storyboard row carries them outside the sentence.
    """
    if _DESCRIPTIVE_RE.search(line or sentence):
        return False
    if _META_CONTEXT_RE.search(sentence) or _REPORTED_SPEECH_RE.search(sentence):
        return False
    start, end = match.span()
    before = sentence[max(0, start - 32):start]
    after = sentence[end:end + 32]
    return not (_NEGATION_BEFORE_RE.search(before) or _NEGATION_AFTER_RE.search(after))


def _claim_sentences(text: str, regex: re.Pattern) -> List[str]:
    out = []
    for line in str(text or "").splitlines():
        for sentence in _sentences(line):
            match = regex.search(sentence)
            if match and _is_assertion(sentence, match, line):
                out.append(sentence.strip())
    return out


def assertion_claims(text: str) -> List[str]:
    """Sentences asserting current completion (test, deploy, visual, or done)."""
    out = []
    for line in str(text or "").splitlines():
        for sentence in _sentences(line):
            for regex in (TEST_CLAIM_RE, DEPLOY_CLAIM_RE, VISUAL_CLAIM_RE, COMPLETION_CLAIM_RE):
                match = regex.search(sentence)
                if match:
                    if _is_assertion(sentence, match, line):
                        out.append(sentence.strip())
                    break
    return out


def claim_targets(text: str) -> Tuple[List[str], List[str]]:
    """Paths and URLs named inside a claim sentence."""
    body = str(text or "")
    return PATH_RE.findall(body), URL_RE.findall(body)


def host_of(url: str) -> str:
    match = re.match(r"https?://([^/:]+)", str(url or ""))
    return match.group(1).lower() if match else ""


def _run_passed(out: str) -> bool:
    """The runner's own result: pass summary or exit=0, never a wrapper's status."""
    text = str(out or "")
    if _RUN_FAILURE_RE.search(text) or _RUN_FAILED_TOKEN_RE.search(text):
        return False
    return bool(EXIT_OK_RE.search(text) or _RUN_PASS_RE.search(text))


def _same_scope(command: str, targets: List[str]) -> bool:
    """Unscoped runs cover any target; a scoped run must mention the claimed one."""
    if not targets:
        return True
    mentioned = PATH_RE.findall(str(command or ""))
    if not mentioned:
        return True
    wanted = {t.split("/")[-1].rsplit(".", 1)[0].lower() for t in targets}
    return any(w and w in m.lower() for w in wanted for m in mentioned)


def _cmd_text(call: Dict[str, Any]) -> str:
    args = call.get("args") or call.get("arguments") or {}
    if isinstance(args, dict):
        for key in ("command", "Command", "cmd", "Cmd"):
            if isinstance(args.get(key), str):
                return args[key]
    return str(args)


# echo/printf/interpreter one-liner chỉ phát lại chữ trong lệnh: tự chứng,
# không bao giờ là receipt.
ECHO_RE = re.compile(
    r"^\s*(?:echo|printf|python3?\s+(?:-c|-m\s+\w+\s+-c)|node\s+-e|ruby\s+-e|perl\s+-e)\b",
    re.I)


def _call_output_pairs(steps: List[Dict[str, Any]]) -> Iterator[Tuple[Dict[str, Any], str]]:
    outputs = {}
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        oid = step.get("tool_call_id") or step.get("call_id")
        if oid:
            outputs[str(oid)] = str(step.get("content") or "")
    for step in steps or []:
        calls = step.get("tool_calls") if isinstance(step, dict) else None
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict) and not ECHO_RE.match(_cmd_text(call)):
                yield call, outputs.get(str(call.get("id") or call.get("tool_call_id") or ""), "")


def _target_state_observed(pairs, targets=()) -> bool:
    """State-check command với output thật (không chỉ exit line, không phải
    output của chính script deploy)."""
    for call, out in pairs:
        cmd = str(call.get("args") or call.get("arguments") or "")
        if not STATE_CHECK_RE.search(cmd) or not _same_scope(cmd, list(targets)):
            continue
        observed = [l for l in str(out or "").splitlines()
                    if l.strip() and not EXIT_LINE_RE.match(l)]
        if observed:
            return True
    return False


def _visual_evidence(steps: List[Dict[str, Any]]) -> Optional[Tuple[str, List[str]]]:
    """(OCR text, symptom flags) or None when a screenshot cannot be read here."""
    try:
        from sage.imgtext import visual_text_and_flags
        visual, flags = visual_text_and_flags(steps)
    except Exception:
        return None
    return visual, flags


def uncovered_claims(reply: str, steps: List[Dict[str, Any]]) -> List[str]:
    """Gaps between asserted claims and receipts of that kind."""
    text = str(reply or "")
    pairs = list(_call_output_pairs(steps))
    gaps: List[str] = []
    test_claims = _claim_sentences(text, TEST_CLAIM_RE)
    if test_claims:
        targets = [p for sentence in test_claims for p in PATH_RE.findall(sentence)]
        covered = False
        for call, out in pairs:
            command = str(call.get("args") or call.get("arguments") or "")
            if TEST_CMD_RE.search(command) and _same_scope(command, targets) and _run_passed(out):
                covered = True
                break
        if not covered:
            gaps.append("test claim: no test-run receipt with a passing result")
    deploy_claims = _claim_sentences(text, DEPLOY_CLAIM_RE)
    if deploy_claims:
        paths = [p for sentence in deploy_claims for p in PATH_RE.findall(sentence)]
        hosts = {host_of(u) for sentence in deploy_claims for u in URL_RE.findall(sentence)}
        hosts.discard("")
        probed = set()
        for call, out in pairs:
            command = str(call.get("args") or call.get("arguments") or "")
            if PROBE_CMD_RE.search(command):
                probed |= {host_of(u) for u in URL_RE.findall(f"{command} {out}")}
        probed.discard("")
        url_covered = bool(hosts & probed) if hosts else bool(probed)
        if not url_covered and not _target_state_observed(pairs, paths):
            gaps.append("deploy claim: no target-state receipt "
                        "(URL/status check, or ls/stat/hash of the deployed target)")
    visual_claims = [s for s in _claim_sentences(text, VISUAL_CLAIM_RE)
                     if VISUAL_PROOF_RE.search(s)]
    if visual_claims:
        evidence = _visual_evidence(steps)
        if evidence is None:
            return gaps  # an unreadable screenshot is unauditable, not missing
        visual, flags = evidence
        if not visual:
            gaps.append("visual claim: no screenshot receipt")
        elif flags:
            gaps.append(f"visual claim: screenshot shows {', '.join(flags)}")
    return gaps


def claim_contract_hint(reply: str, steps: List[Dict[str, Any]]) -> Optional[str]:
    """not_verified steer shaped like the Compass hint; None when covered or unaudited."""
    if not steps:
        return None
    gaps = uncovered_claims(reply, steps)
    if not gaps:
        return None
    criterion = (COMPASS_CATEGORIES.get("not_verified") or {}).get(
        "criterion", "A material outcome lacks evidence")
    return f"jev_compass not_verified: {criterion} Evidence: " + "; ".join(gaps)


def claim_contract_hint_for(reply: str, transcript_path: str) -> Optional[str]:
    """Hook entry: same hint, steps read from a transcript path."""
    from sage.transcript import _read_transcript_steps
    try:
        steps = _read_transcript_steps(transcript_path)
    except Exception:
        return None
    return claim_contract_hint(reply, steps)
