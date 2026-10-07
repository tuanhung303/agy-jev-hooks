"""Link receipts to the operation a claim names.

A script's output is the script's own, so the gate cannot read it as a test
or deploy receipt. It makes evidence unknown only when the script itself
names the claimed operation; an unrelated script never silences a claim.
"""
import re

from sage.claim_receipts import INSPECTION_RE, _cmd_text, _program, _stages, is_execution

# Stages that print text about earlier runs without running anything.
_NOT_RUNNERS = {"git", "gh", "echo", "printf", "cd", "export", "source", "set", "true", "false"}
_SCRIPT_PATH_RE = re.compile(r"[\w./~-]+\.(?:sh|py|c?m?js|ts)\b", re.I)
_CHECKER_NAME_RE = re.compile(
    r"(?:^|[_./-])(?:tests?|spec|accept\w*|verify|validate|validator|smoke|checks?|e2e|audit|lint)"
    r"(?:[_./-]|$)", re.I)
_MAKE_TEST_RE = re.compile(r"(?:^|[\s;&|(\x22'])make\s+(?:test|tests|check|verify|e2e|smoke)\b", re.I)
_TEST_OUTPUT_RE = re.compile(
    r"\b\d+\s+tests?\b(?!\s+(?:rows?|records?|data|files?|users?|accounts?|fixtures?))|"
    r"\btests?\s+(?:pass(?:ed)?|ok)\b|\ball tests\b|\bACCEPT-(?:OK|FAIL)\b|"
    r"\b\d+\s+(?:passed|PASS)\b[^\n]{0,40}\b\d+\s+(?:failed|FAIL)\b|\b\d+\s*/\s*\d+\s+(?:passed|PASS)\b", re.I)
# Linters and formatters report style, never test results.
_NOT_TEST_PROGRAMS = {"ruff", "eslint", "prettier", "black", "mypy", "flake8", "pylint", "tsc", "isort"}
_GENERIC_PROGRAMS = _NOT_RUNNERS | _NOT_TEST_PROGRAMS | {
    "npm", "npx", "node", "python", "python3", "uv", "uvx", "bash", "sh", "zsh", "make", "pnpm",
    "yarn", "bun", "deno", "go", "cargo", "env", "sudo", "time", "timeout", "cat", "ls"}
DEPLOY_ACTION_RE = re.compile(
    r"\bdeploy|\bgit\s+push\b|\bvercel\b|\bwrangler\b|\bkubectl\s+apply\b|\brsync\b|\bscp\b|"
    r"(?:^|[\s;&|(\x22'])cp\s", re.I)
_VERIFY_SCRIPT_RE = re.compile(r"(?:^|[/_.-])(?:capture|verify|check|smoke|probe|e2e)(?:[/_.-]|$)", re.I)


def _programs(command):
    return [(stage, _program(stage).strip("\"'").lower()) for stage in _stages(command)]


def runs_tests(call, output, runner_re, summary_re) -> bool:
    """An executed stage runs a test runner, or prints a runner summary itself."""
    if not is_execution(call):
        return False
    stages = [(s, p) for s, p in _programs(_cmd_text(call))
              if p not in _NOT_RUNNERS and not INSPECTION_RE.match(p)]
    if str(call.get("name") or "").lower() != "manage_task" and any(
            runner_re.search(stage) for stage, _p in stages):
        return True
    return bool(stages) and bool(summary_re.search(str(output or "")))


def _script_names(command):
    return [path.rsplit("/", 1)[-1].lower() for path in _SCRIPT_PATH_RE.findall(str(command or ""))]


def test_linked(command, output) -> bool:
    """A script whose path or output says it runs tests or checks."""
    paths = [p.lower().rsplit(".", 1)[0] for p in _SCRIPT_PATH_RE.findall(str(command or ""))]
    return (any(_CHECKER_NAME_RE.search(path) for path in paths)
            or bool(_MAKE_TEST_RE.search(command)) or bool(_TEST_OUTPUT_RE.search(str(output or ""))))


def named_check_passed(sentence, pairs, passed) -> bool:
    """The claim names the checker it ran (a script or a tool), and that run passed."""
    names = {w.lower() for w in re.findall(r"[A-Za-z][\w.-]{3,}", str(sentence or ""))}
    for call, output, result in pairs:
        if not is_execution(call) or not passed(output, result):
            continue
        for stage, program in _programs(_cmd_text(call)):
            scripts = [s for s in _script_names(stage) if _CHECKER_NAME_RE.search(s.rsplit(".", 1)[0])]
            if (program not in _GENERIC_PROGRAMS and program in names) or any(s in names for s in scripts):
                return True
    return False


def verify_script_passed(call, output, result, status_ok) -> bool:
    """A capture or verification script that exited cleanly observed the target."""
    if not is_execution(call):
        return False
    return any(_VERIFY_SCRIPT_RE.search(name.rsplit(".", 1)[0]) for name in _script_names(_cmd_text(call))) \
        and bool(str(output or "").strip()) and status_ok(output, result)


def deploy_attempted(pairs) -> bool:
    return any(is_execution(call) and str(call.get("name") or "").lower() != "manage_task"
               and DEPLOY_ACTION_RE.search(_cmd_text(call)) for call, _o, _r in pairs)


def unreadable_turn(pairs) -> bool:
    """Every execution lost its capture (not a pending background task), and at
    least one could have done the work: the turn is unauditable."""
    runs = [(call, result) for call, _output, result in pairs if is_execution(call)]
    return bool(runs) and all(
        result.get("_capture_ambiguous") and not result.get("_background_task")
        and not result.get("content") for _call, result in runs) and any(
        not INSPECTION_RE.match(program) for call, _result in runs
        for _stage, program in _programs(_cmd_text(call)))
