"""Receipt rules for test and deploy claims, and Kai's carry-over rule.

Test claims need a passing run of a test runner (or a runner summary from an
executed command) inside the claimed scope; deploy claims need an
observation of the target. A claim this turn did not attempt may restate an
earlier turn's result, covered only by that earlier passing receipt.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from sage.claim_links import (
    deploy_attempted, named_check_passed, runs_tests, test_linked, verify_script_passed,
)
from sage.claim_receipts import SCRIPT_RE, _cmd_text, is_execution
from sage.jev.evidence.attribution import status_of
from sage.pipeline_claims import _status_ok, receipt_covers
from sage.pipeline_receipts import run_observation

TEST_CMD_RE = re.compile(
    r"\b(?:pytest|unittest|jest|vitest|mocha|rspec|phpunit|node\s+--test|npm (?:run )?test|"
    r"yarn test|pnpm (?:run )?test|bun test|deno test|tsx\s+--test|make test|tox|go test|cargo test|"
    r"ctest)\b", re.I)
# Runner-agnostic summaries: a script that prints one of these ran tests.
TEST_SUMMARY_RE = re.compile(
    r"#\s*pass\s+\d+\s+#\s*fail\s+0\b|\bRan \d+ tests? in [\d.]+s\s+OK\b|"
    r"\b\d+ passed\b[^\n]{0,60}\bin [\d.]+s\b|\bTests?:\s+\d+ passed\b", re.I)
# A successful script whose output the gate cannot classify is unauditable.
TEST_WRAPPER_RE = re.compile(SCRIPT_RE.pattern + r"|(?:^|[\s;&|(\x22'])make\s+\w+", re.I)
EXIT_OK_RE = re.compile(r"exit[=: ]+0\b|exited with code 0\b", re.I)
URL_RE = re.compile(r"https?://[^\s)\]]+")
PATH_RE = re.compile(
    r"[\w./~-]+\.(?:py|ts|tsx|js|jsx|mjs|json|ya?ml|toml|sh|sql|md|css|html?)\b", re.I)
# Deploy claims need a receipt that checked the target, not any URL that
# happens to appear in the turn's output.
PROBE_CMD_RE = re.compile(
    r"\b(?:curl|wget|httpie|http|nc|ping|dig|nslookup|openssl)\b|\bfetch\s*\(|\.goto\s*\(", re.I)
# Deploy local không có URL: receipt phải là quan sát target state (ls/stat/hash/diff).
STATE_CHECK_RE = re.compile(r"\b(?:ls|stat|shasum|sha\d+sum|md5|diff|cmp)\b", re.I)
EXIT_LINE_RE = re.compile(r"^\s*exit[=: ]+\d+\s*$", re.I)

# A runner failure summary outranks any exit token, and a pass-count summary
# counts like exit=0 (piping hides the runner's own status behind the wrapper's).
# The uppercase token stays case-sensitive: runner logs print "failed" freely.
_RUN_FAILURE_RE = re.compile(
    r"\b[1-9]\d*\s+(?:failed|failing|errors?)\b|\bAssertionError\b|\bTraceback\b|"
    r"\berror(?:s)? during collection\b|\bno tests ran\b|#\s*fail\s+[1-9]\d*|\bnpm ERR!", re.I)
_RUN_FAILED_TOKEN_RE = re.compile(r"\bFAILED\b")
_RUN_PASS_RE = re.compile(
    r"\b\d+\s+(?:passed|passing|tests? pass(?:ed)?)\b|\btests? passed\b|#\s*pass\s+\d+|"
    r"\b\d+\s*/\s*\d+\b|\[\s*100%\s*\]|\bRan \d+ tests?\b|^OK$", re.I | re.M)


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


def _unclassified_test_wrapper(call, out, result, targets) -> bool:
    """A test-shaped script that exited 0, or any script whose result is unknown."""
    command = _cmd_text(call)
    if not is_execution(call) or not TEST_WRAPPER_RE.search(command) or not _same_scope(command, targets):
        return False
    if not test_linked(command, out):
        return False  # an unrelated script never silences a test claim
    if (result or {}).get("_capture_ambiguous"):
        return True  # a test script with an unknown result may have run the tests
    text = str(out or "")
    if _RUN_FAILURE_RE.search(text) or _RUN_FAILED_TOKEN_RE.search(text):
        return False
    return status_of(result or {}, text) == "0" or bool(EXIT_OK_RE.search(text))


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


def _test_gap(test_claims, pairs) -> Tuple[bool, bool]:
    """(covered, attempted) for test claims over one set of receipts."""
    targets = [p for sentence in test_claims for p in PATH_RE.findall(sentence)]
    attempted = covered = False
    for call, out, result in pairs:
        command = _cmd_text(call)
        if runs_tests(call, out, TEST_CMD_RE, TEST_SUMMARY_RE) and _same_scope(command, targets):
            attempted = True
            covered = covered or _run_passed(out, result)
    if not covered:
        covered = any(named_check_passed(s, pairs, _run_passed) for s in test_claims)
    uncertain = any(is_execution(call) and TEST_CMD_RE.search(_cmd_text(call))
                    and _same_scope(_cmd_text(call), targets) and result.get("_capture_ambiguous")
                    for call, _out, result in pairs) or any(
        _unclassified_test_wrapper(call, out, result, targets) for call, out, result in pairs)
    return covered or uncertain, attempted or uncertain


def _deploy_gap(deploy_claims, pairs) -> Tuple[bool, bool]:
    """(covered, attempted) for deploy claims over one set of receipts."""
    paths = [p for sentence in deploy_claims for p in PATH_RE.findall(sentence)]
    hosts = {host_of(u) for sentence in deploy_claims for u in URL_RE.findall(sentence)}
    hosts.discard("")
    probed = set()
    for call, out, _result in pairs:
        command = _cmd_text(call)
        if is_execution(call) and PROBE_CMD_RE.search(command):
            probed |= {host_of(u) for u in URL_RE.findall(f"{command} {out}")}
    probed.discard("")
    url_covered = bool(hosts & probed) if hosts else bool(probed)
    pull_request = any(re.search(r"\b(?:PR|pull request)\b", s) for s in deploy_claims)
    observed = not hosts and any(
        verify_script_passed(call, out, result, _status_ok) or (
            is_execution(call) and re.search(r"\bgh\s+run\s+(?:watch|view)\b", _cmd_text(call))
            and re.search(r"deploy", out, re.I) and run_observation(out) == "success") or (
            pull_request and is_execution(call) and re.search(r"\bgh\s+pr\s+(?:view|create|list|checks)\b",
                                                             _cmd_text(call)) and out.strip()
            and _status_ok(out, result))
        for call, out, result in pairs)
    observation_missing = any(
        result.get("_capture_ambiguous") and is_execution(call) and (
            (PROBE_CMD_RE.search(_cmd_text(call)) and (not hosts or hosts & {
                host_of(u) for u in URL_RE.findall(_cmd_text(call))}))
            or (STATE_CHECK_RE.search(_cmd_text(call)) and _same_scope(_cmd_text(call), paths))
            or re.search(r"\bdeploy|\b(?:describe|status|watch)\b", _cmd_text(call), re.I))
        for call, _out, result in pairs)
    covered = url_covered or observed or observation_missing or _target_state_observed(pairs, paths)
    return covered, bool(probed) or observation_missing or deploy_attempted(pairs)


_CLAIMED_COUNT_RE = re.compile(r"\b(\d[\d,]*)\s*/\s*\1\b|\ball\s+(?=\d)(\d[\d,]*)|\b(\d[\d,]*)\s+(?:\w+\s+)?tests?\b")


def _carried(kind, claims_of_kind, prior_pairs) -> bool:
    """Kai's carry-over rule: an earlier turn's passing receipt for the same operation."""
    if kind == "test":
        # A stated count must appear in the earlier passing run it restates.
        counts = {n.replace(",", "") for s in claims_of_kind
                  for groups in _CLAIMED_COUNT_RE.findall(s) for n in groups if n}
        runs = [o.replace(",", "") for c, o, r in prior_pairs
                if runs_tests(c, o, TEST_CMD_RE, TEST_SUMMARY_RE) and _run_passed(o, r)]
        return bool(runs) and all(any(re.search(rf"\b{n}\b", o) for o in runs) for n in counts) or (
            not counts and any(named_check_passed(s, prior_pairs, _run_passed) for s in claims_of_kind))
    if kind == "deploy":
        # Only a named target that an earlier turn observed carries over.
        hosts = {host_of(u) for sentence in claims_of_kind for u in URL_RE.findall(sentence)} - {""}
        paths = [p for sentence in claims_of_kind for p in PATH_RE.findall(sentence)]
        probed = {host_of(u) for c, o, r in prior_pairs if is_execution(c)
                  and PROBE_CMD_RE.search(_cmd_text(c)) and not r.get("_capture_ambiguous")
                  for u in URL_RE.findall(f"{_cmd_text(c)} {o}")}
        return bool(hosts & probed) or bool(paths and _target_state_observed(prior_pairs, paths))
    return receipt_covers(claims_of_kind, prior_pairs, _cmd_text, EXIT_OK_RE, strict=True)
