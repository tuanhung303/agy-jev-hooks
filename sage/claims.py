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
from typing import Any, Dict, List, Optional, Tuple

from sage.jev.config.catalog import COMPASS_CATEGORIES
from sage.claim_assertions import _claim_sentences, _is_assertion, _sentences
from sage.pipeline_claims import (
    BUILD_CLAIM_RE, COMPILE_CLAIM_RE, DATA_CLAIM_RE, DBT_CLAIM_RE, PIPELINE_CLAIM_RE,
    attempts_operation, has_execution_or_edit, receipt_covers,
)
from sage.jev.evidence.attribution import status_of
from sage.claim_receipts import _call_output_pairs, _cmd_text
TEST_CLAIM_RE = re.compile(
    r"\btests? (?:pass|passed|are green|all green|run clean)\b|\b\d+ tests? passed\b"
    r"|\ball tests pass\b|\btest run (?:pass(?:ed)?|succeed(?:ed)?|completed)\b|"
    r"kiểm thử[^.]* (?:đạt|pass)", re.I)
DEPLOY_CLAIM_RE = re.compile(
    r"\b(?:deployed|is live|in production|published)\b|đã (?:deploy|lên sóng)", re.I)
DEPLOY_DESCRIPTION_RE = re.compile(
    r"\bpublished\s+(?:measures?|reruns?|attempts?|events?|statuses?|records?|rows?|rules?|tables?)\b|"
    r"\bpublication status\b|\bevaluates?\s+to\s+published\b", re.I)
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
EXIT_OK_RE = re.compile(r"exit[=: ]+0\b|exited with code 0\b", re.I)
URL_RE = re.compile(r"https?://[^\s)\]]+")
PATH_RE = re.compile(
    r"[\w./~-]+\.(?:py|ts|tsx|js|jsx|mjs|json|ya?ml|toml|sh|sql|md|css|html?)\b", re.I)
# Deploy claims need a receipt that checked the target, not any URL that
# happens to appear in the turn's output.
PROBE_CMD_RE = re.compile(r"\b(?:curl|wget|httpie|http|nc|ping|dig|nslookup|openssl)\b", re.I)
# Deploy local không có URL: receipt phải là quan sát target state (ls/stat/hash/diff).
STATE_CHECK_RE = re.compile(r"\b(?:ls|stat|shasum|sha\d+sum|md5|diff|cmp)\b", re.I)
EXIT_LINE_RE = re.compile(r"^\s*exit[=: ]+\d+\s*$", re.I)

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
    return match.group(1).lower().rstrip(".") if match else ""


def _run_passed(out: str, result_step: Optional[Dict[str, Any]] = None) -> bool:
    """The runner's own result: pass summary or exit=0, never a wrapper's status."""
    text = str(out or "")
    if _RUN_FAILURE_RE.search(text) or _RUN_FAILED_TOKEN_RE.search(text):
        return False
    status = status_of(result_step or {}, text)
    if status not in ("unknown", "0"):
        return False
    return bool(status == "0" or EXIT_OK_RE.search(text) or _RUN_PASS_RE.search(text))


def _same_scope(command: str, targets: List[str]) -> bool:
    """Unscoped runs cover any target; a scoped run must mention the claimed one."""
    if not targets:
        return True
    mentioned = PATH_RE.findall(str(command or ""))
    if not mentioned:
        return True
    wanted = {t.split("/")[-1].rsplit(".", 1)[0].lower() for t in targets}
    return any(w and w in m.lower() for w in wanted for m in mentioned)


def _target_state_observed(pairs, targets=()) -> bool:
    """State-check command với output thật (không chỉ exit line, không phải
    output của chính script deploy)."""
    for call, out, _result in pairs:
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
    pairs, ambiguous = _call_output_pairs(steps)
    gaps: List[str] = []
    test_claims = [sentence for sentence in _claim_sentences(text, TEST_CLAIM_RE)
                   if not re.search(r"\bdbt\s+test\b", sentence, re.I)]
    if test_claims:
        targets = [p for sentence in test_claims for p in PATH_RE.findall(sentence)]
        covered = False
        for call, out, result in pairs:
            command = str(call.get("args") or call.get("arguments") or "")
            if TEST_CMD_RE.search(command) and _same_scope(command, targets) and _run_passed(out, result):
                covered = True
                break
        uncertain = any(TEST_CMD_RE.search(_cmd_text(call))
                        and _same_scope(_cmd_text(call), targets)
                        and result.get("_capture_ambiguous")
                        for call, _out, result in pairs)
        if not covered and not uncertain:
            gaps.append("test claim: no test-run receipt with a passing result")
    deploy_claims = [s for s in _claim_sentences(text, DEPLOY_CLAIM_RE)
                     if not DEPLOY_DESCRIPTION_RE.search(s)]
    if deploy_claims:
        paths = [p for sentence in deploy_claims for p in PATH_RE.findall(sentence)]
        hosts = {host_of(u) for sentence in deploy_claims for u in URL_RE.findall(sentence)}
        hosts.discard("")
        probed = set()
        for call, out, _result in pairs:
            command = str(call.get("args") or call.get("arguments") or "")
            if PROBE_CMD_RE.search(command):
                probed |= {host_of(u) for u in URL_RE.findall(f"{command} {out}")}
        probed.discard("")
        url_covered = bool(hosts & probed) if hosts else bool(probed)
        observation_missing = any(
            result.get("_capture_ambiguous") and (
                (PROBE_CMD_RE.search(_cmd_text(call)) and (not hosts or hosts & {
                    host_of(u) for u in URL_RE.findall(_cmd_text(call))}))
                or (STATE_CHECK_RE.search(_cmd_text(call)) and _same_scope(_cmd_text(call), paths)))
            for call, _out, result in pairs)
        if not url_covered and not observation_missing and not _target_state_observed(pairs, paths):
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
    pipeline_claims = []
    for regex in (DBT_CLAIM_RE, BUILD_CLAIM_RE, PIPELINE_CLAIM_RE, COMPILE_CLAIM_RE,
                  DATA_CLAIM_RE):
        pipeline_claims.extend(s for s in _claim_sentences(text, regex) if s not in pipeline_claims)
    # Each assertion is judged alone: uncertainty about one operation never
    # erases a captured failure of another.
    for sentence in pipeline_claims:
        uncertain = any(result.get("_capture_ambiguous")
                        and attempts_operation(sentence, _cmd_text(call), pairs)
                        for call, _out, result in pairs)
        if not uncertain and not receipt_covers([sentence], pairs, _cmd_text, EXIT_OK_RE):
            gaps.append("pipeline/data claim: no matching run, compile, or count-query receipt")
            break
    return gaps


def claim_contract_hint(reply: str, steps: List[Dict[str, Any]],
                        require_action: bool = False) -> Optional[str]:
    """not_verified steer shaped like the Compass hint; None when covered or unaudited."""
    if not steps or (require_action and not has_execution_or_edit(steps)):
        return None
    gaps = uncovered_claims(reply, steps)
    if not gaps:
        return None
    criterion = (COMPASS_CATEGORIES.get("not_verified") or {}).get(
        "criterion", "A material outcome lacks evidence")
    return f"jev_compass not_verified: {criterion} Evidence: " + "; ".join(gaps)


def claim_contract_hint_for(reply: str, transcript_path: str,
                            require_action: bool = False,
                            steps: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Hook entry: same hint, steps read from a transcript path."""
    try:
        if steps is None:
            from sage.transcript import _read_transcript_steps
            steps = _read_transcript_steps(transcript_path)
            from sage.transcript import is_explicit_user_input
            starts = [i for i, step in enumerate(steps) if is_explicit_user_input(step)]
            if not starts:
                return None
            steps = steps[starts[-1]:]
        return claim_contract_hint(reply, steps, require_action=require_action)
    except Exception:
        return None
