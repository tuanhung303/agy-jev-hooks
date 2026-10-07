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
    _claimed_run_id, attempts_operation, has_execution_or_edit, names_pipeline_run, receipt_covers,
)
from sage.run_logs import finished_outputs, subagent_reported
from sage.claim_links import unreadable_turn
from sage.pipeline_receipts import run_observation
from sage.claim_receipts import _call_output_pairs, _cmd_text, is_execution
from sage.claim_rules import (  # noqa: F401  (receipt rules re-exported for callers)
    EXIT_OK_RE, PATH_RE, PROBE_CMD_RE, STATE_CHECK_RE, TEST_CMD_RE, TEST_SUMMARY_RE, URL_RE,
    _carried, _deploy_gap, _run_passed, _same_scope, _target_state_observed, _test_gap,
    _unclassified_test_wrapper, host_of,
)

TEST_CLAIM_RE = re.compile(
    r"\btests? (?:pass|passed|are green|all green|run clean)\b|\b\d+ tests? passed\b"
    r"|\ball tests pass\b|\btest run (?:pass(?:ed)?|succeed(?:ed)?|completed)\b|"
    r"kiểm thử[^.]* (?:đạt|pass)", re.I)
DEPLOY_CLAIM_RE = re.compile(
    r"\b(?:deployed|is live|in production|published)\b|đã (?:deploy|lên sóng)", re.I)
DEPLOY_DESCRIPTION_RE = re.compile(
    r"\bpublished\s+(?:measures?|reruns?|attempts?|events?|statuses?|records?|rows?|rules?|tables?)\b|"
    r"\bpublication status\b|\bevaluates?\s+to\s+published\b|\b(?:logged|recorded)\s+and\s+published\b", re.I)
# Narrative about a named third party ("Acme deployed X") is not our claim.
THIRD_PARTY_DEPLOY_RE = re.compile(
    r"\b(?!(?:I|We|It|This|That|The|Code|App|Service|Build)\b)([A-Z][\w-]+)\s+(?:has\s+|had\s+)?deployed\b")


def _third_party(sentence: str) -> bool:
    """A named subject inside the sentence; a sentence-initial word or adverb is ours."""
    return any(not m.group(1).lower().endswith("ly") and sentence[:m.start()].strip(" -*>#`'\"")
               for m in THIRD_PARTY_DEPLOY_RE.finditer(sentence))
VISUAL_CLAIM_RE = re.compile(r"\b(?:screenshot|screen shot)\b|ảnh chụp", re.I)
# Chỉ claim khi reply gắn ảnh với chứng cứ do chính nó tạo, không phải ảnh
# người dùng gửi hay ảnh được nhắc tới trong câu chuyện.
VISUAL_PROOF_RE = re.compile(
    r"\b(?:proof|evidence|attached|verified|chứng minh|đính kèm|dẫn chứng|chụp)\b|"
    r"\.(?:png|jpe?g|webp|gif)\b", re.I)
COMPLETION_CLAIM_RE = re.compile(
    r"\b(?:done|complete[d]?|finished|fixed|implemented|shipped|verified|checked|added|created|"
    r"updated|migrated|xong|hoàn tất|hoàn thành|đã)\b", re.I)
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


def _visual_evidence(steps: List[Dict[str, Any]]) -> Optional[Tuple[str, List[str]]]:
    """(OCR text, symptom flags) or None when a screenshot cannot be read here."""
    try:
        from sage.imgtext import visual_text_and_flags
        visual, flags = visual_text_and_flags(steps)
    except Exception:
        return None
    return visual, flags


def _last_polled(sentence: str, steps) -> str:
    """Outcome of the latest system-delivered poll log for the run this claim names by ID."""
    wanted = _claimed_run_id(sentence)
    if not wanted:
        return ""
    seen = [run_observation(body, wanted) for body in finished_outputs(steps)]
    seen = [outcome for outcome in seen if outcome not in ("absent", "unknown")]
    return seen[-1] if seen else ""


def uncovered_claims(reply: str, steps: List[Dict[str, Any]],
                     prior_steps: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Gaps between asserted claims and receipts of that kind.

    prior_steps are the session's earlier turns. A claim this turn did not
    attempt may restate an earlier result; it is covered only by an earlier
    passing receipt for the same operation.
    """
    text = str(reply or "")
    pairs, _ambiguous = _call_output_pairs(steps)
    if unreadable_turn(pairs):
        return []  # no capture of any execution survives: unauditable, not missing
    prior = None
    def carried(kind, claims_of_kind):
        nonlocal prior
        if not prior_steps:
            return False
        if prior is None:
            prior = _call_output_pairs(prior_steps)[0]
        if kind == "pipeline" and all(_last_polled(s, prior_steps) == "success" for s in claims_of_kind):
            return True  # the latest poll log the system delivered for that run succeeded
        return _carried(kind, claims_of_kind, prior)
    gaps: List[str] = []
    test_claims = [sentence for sentence in _claim_sentences(text, TEST_CLAIM_RE)
                   if not re.search(r"\bdbt\s+test\b", sentence, re.I) and not names_pipeline_run(sentence)]
    if test_claims:
        covered, attempted = _test_gap(test_claims, pairs)
        if not covered and (attempted or not carried("test", test_claims)):
            gaps.append("test claim: no test-run receipt with a passing result")
    deploy_claims = [s for s in _claim_sentences(text, DEPLOY_CLAIM_RE)
                     if not DEPLOY_DESCRIPTION_RE.search(s) and not _third_party(s)]
    if deploy_claims:
        covered, attempted = _deploy_gap(deploy_claims, pairs)
        if not covered and (attempted or not carried("deploy", deploy_claims)):
            gaps.append("deploy claim: no target-state receipt "
                        "(URL/status check, or ls/stat/hash of the deployed target)")
    visual_claims = [s for s in _claim_sentences(text, VISUAL_CLAIM_RE)
                     if VISUAL_PROOF_RE.search(VISUAL_CLAIM_RE.sub(" ", s))]
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
        # A status read attempts a pipeline claim only when it observed some run.
        # agy's own task status reads never observe a pipeline run.
        attempted = any(is_execution(call) and str(call.get("name") or "").lower() != "manage_task"
                        and attempts_operation(sentence, _cmd_text(call), pairs)
                        and not (PIPELINE_CLAIM_RE.search(sentence) and run_observation(out) == "absent")
                        for call, out, _result in pairs)
        uncertain = any(result.get("_capture_ambiguous") and is_execution(call)
                        and attempts_operation(sentence, _cmd_text(call), pairs)
                        for call, _out, result in pairs) or subagent_reported(sentence, steps)
        if not uncertain and not receipt_covers([sentence], pairs, _cmd_text, EXIT_OK_RE) and (
                attempted or not carried("pipeline", [sentence])):
            gaps.append("pipeline/data claim: no matching run, compile, or count-query receipt")
            break
    return gaps


# Claim kinds that only log (WOULD_CLAIM) and never block, even when CLAIM is a
# block tag. A kind is listed when its precision on the real-transcript sweep is
# below 80%, counting undecidable flags as false. Round 8: deploy 2 of 4
# (03cd1966:9, 26d9d153:9 true; 03cd1966:40, 6cd6eef0:10 undecidable).
LOG_ONLY_CLAIM_KINDS = frozenset({"deploy"})


def _hint(gaps: List[str]) -> str:
    criterion = (COMPASS_CATEGORIES.get("not_verified") or {}).get(
        "criterion", "A material outcome lacks evidence")
    return f"jev_compass not_verified: {criterion} Evidence: " + "; ".join(gaps)


def claim_contract_verdict(reply: str, steps: List[Dict[str, Any]],
                           require_action: bool = False,
                           prior_steps: Optional[List[Dict[str, Any]]] = None) -> Tuple[Optional[str], bool]:
    """(hint, may_block): blocking gaps when any, else log-only gaps that never block."""
    if not steps or (require_action and not has_execution_or_edit(steps)):
        return None, False
    gaps = uncovered_claims(reply, steps, prior_steps=prior_steps)
    blocking = [gap for gap in gaps if gap.split(" ", 1)[0] not in LOG_ONLY_CLAIM_KINDS]
    if blocking:
        return _hint(blocking), True
    return (_hint(gaps), False) if gaps else (None, False)


def claim_contract_hint(reply: str, steps: List[Dict[str, Any]],
                        require_action: bool = False,
                        prior_steps: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """not_verified steer shaped like the Compass hint; None when covered or unaudited."""
    if not steps or (require_action and not has_execution_or_edit(steps)):
        return None
    gaps = uncovered_claims(reply, steps, prior_steps=prior_steps)
    return _hint(gaps) if gaps else None


def claim_contract_hint_for(reply: str, transcript_path: str,
                            require_action: bool = False,
                            steps: Optional[List[Dict[str, Any]]] = None,
                            prior_steps: Optional[List[Dict[str, Any]]] = None,
                            verdict: bool = False):
    """Hook entry: same hint, steps read from a transcript path.

    verdict=True returns (hint, may_block) from claim_contract_verdict instead.
    """
    empty = (None, False) if verdict else None
    try:
        if steps is None:
            from sage.transcript import _read_transcript_steps
            steps = _read_transcript_steps(transcript_path)
            from sage.transcript import is_explicit_user_input
            starts = [i for i, step in enumerate(steps) if is_explicit_user_input(step)]
            if not starts:
                return empty
            steps, prior_steps = steps[starts[-1]:], steps[:starts[-1]]
        judge = claim_contract_verdict if verdict else claim_contract_hint
        return judge(reply, steps, require_action=require_action, prior_steps=prior_steps)
    except Exception:
        return empty
