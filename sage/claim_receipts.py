"""Bind claim checks to captured tool executions and their outputs."""
import ast
import re
import shlex
from typing import Any, Dict, List, Tuple

_ECHO_RE = re.compile(r"^\s*(?:echo|printf)\b", re.I)
_AGY_RESULT_RE = re.compile(r"^\s*Created At:", re.I)
_BACKGROUND_RE = re.compile(r"Tool is running as a background task with task id:\s*(\S+)", re.I)
_EXIT_CODE_RE = re.compile(r"exited with code \d+|exit[=: ]+\d+", re.I)
_EXPLICIT_FAILURE_RE = re.compile(
    r"#\s*fail\s+[1-9]|\b[1-9]\d*\s+(?:failed|failing)\b|\bERROR\s*=\s*[1-9]", re.I)


def _cmd_text(call: Dict[str, Any]) -> str:
    args = call.get("args") or call.get("arguments") or {}
    if isinstance(args, dict):
        for key in ("command", "Command", "cmd", "Cmd", "CommandLine", "command_line",
                    "commandLine", "script", "Script", "path", "file", "filename"):
            if isinstance(args.get(key), str):
                return args[key]
    return str(args)


def _is_echo_command(command: str) -> bool:
    if _ECHO_RE.match(command):
        return True
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if len(argv) >= 3 and re.fullmatch(r"python3?", argv[0], re.I) and argv[1] == "-c":
        try:
            tree = ast.parse(argv[2])
        except SyntaxError:
            return False
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
            return False
        call = tree.body[0].value
        if not isinstance(call, ast.Call):
            return False
        is_print = isinstance(call.func, ast.Name) and call.func.id == "print"
        is_write = (isinstance(call.func, ast.Attribute) and call.func.attr == "write"
                    and isinstance(call.func.value, ast.Attribute)
                    and call.func.value.attr == "stdout")
        return (is_print or is_write) and not call.keywords and all(
            isinstance(arg, ast.Constant) and isinstance(arg.value, (str, int, float))
            for arg in call.args)
    if len(argv) >= 3 and argv[0] == "node" and argv[1] == "-e":
        return bool(re.fullmatch(
            r"\s*(?:console\.log|process\.stdout\.write)\s*\(\s*(['\"])(?:\\.|(?!\1).)*\1\s*\)\s*;?\s*",
            argv[2], re.S))
    return False


def _call_id(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("tool_call_id") or item.get("call_id") or "")


def _output_id(step: Dict[str, Any]) -> str:
    meta = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
    return str(step.get("tool_call_id") or step.get("call_id") or meta.get("tool_call_id")
               or meta.get("call_id") or "")


def _background_result(steps, start, task_id):
    """The completion record of a background task, or an uncertain capture.

    A native background launch returns only a task handle. Its outcome comes
    later, from a `Task id ... finished with result` system message or a
    `manage_task` status read with `Status: DONE` for the same task ID.
    """
    finished, status_done = "", ""
    escaped = re.escape(task_id)
    for step in steps[start + 1:]:
        if not isinstance(step, dict):
            continue
        text = str(step.get("content") or "")
        if task_id not in text:
            continue
        match = re.search(rf"Task id \"?{escaped}\"? finished with result:?(.*)", text, re.S)
        if match:
            finished = match.group(1).split("</SYSTEM_MESSAGE>")[0].strip()
            continue
        if re.search(rf"^Task:\s*{escaped}\s*$", text, re.M) and re.search(r"^Status:\s*DONE\b", text, re.M):
            status_done = text
    content = "\n".join(part for part in (finished, status_done) if part)
    if not content:
        return {"_capture_ambiguous": True, "_background_task": task_id}
    result = {"content": content, "_background_task": task_id}
    if not _EXIT_CODE_RE.search(content) and not _EXPLICIT_FAILURE_RE.search(content):
        # A DONE status without an exit code proves completion, not outcome:
        # a pass summary can still cover, but absence of one is not a gap.
        result["_capture_ambiguous"] = True
    return result


def _call_output_pairs(steps: List[Dict[str, Any]]) -> Tuple[List[Tuple[Dict[str, Any], str, Dict[str, Any]]], bool]:
    """Pair each call with its result; uncertain pairs carry _capture_ambiguous.

    Explicit IDs bind by identity and are never positional candidates. An
    ID-less native result binds by position only when exactly one call is
    outstanding. Results that arrive while several ID-less calls are
    outstanding form an overlap group whose members stay uncertain; serial
    binding resumes once the group has received one result per member.
    """
    outputs: Dict[str, Dict[str, Any]] = {}
    duplicate_ids = set()
    seen_call_ids = set()
    calls = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        oid = _output_id(step)
        if oid:
            if oid in outputs:
                duplicate_ids.add(oid)
            outputs[oid] = step
        tool_calls = step.get("tool_calls")
        for item in tool_calls if isinstance(tool_calls, list) else []:
            if not isinstance(item, dict):
                continue
            cid = _call_id(item)
            if cid and cid in seen_call_ids:
                duplicate_ids.add(cid)
            elif cid:
                seen_call_ids.add(cid)
            calls.append(item)

    positional: Dict[int, Dict[str, Any]] = {}
    tentative: List[Tuple[Dict[str, Any], Dict[str, Any], set]] = []
    open_idless: List[Dict[str, Any]] = []
    open_ids: set = set()
    group: List[Dict[str, Any]] = []
    group_results = 0

    def mark(items):
        for item in items:
            positional[id(item)] = {"_capture_ambiguous": True}

    for step in steps or []:
        if not isinstance(step, dict):
            continue
        stype = str(step.get("type") or "").upper()
        if stype == "USER_INPUT":
            mark(open_idless + group)
            open_idless, open_ids, group, group_results = [], set(), [], 0
            continue
        oid = _output_id(step)
        if oid:
            open_ids.discard(oid)
        tool_calls = step.get("tool_calls")
        for item in tool_calls if isinstance(tool_calls, list) else []:
            if not isinstance(item, dict):
                continue
            cid = _call_id(item)
            if cid:
                open_ids.add(cid)
            elif group:
                group.append(item)
            else:
                open_idless.append(item)
        if oid or stype != "GENERIC" or tool_calls:
            continue
        # An ID-less result step.
        if not group and len(open_idless) > 1:
            group, open_idless, group_results = open_idless, [], 0
        if group:
            group_results += 1
            if group_results >= len(group):
                mark(group)
                group, group_results = [], 0
            continue
        if not open_idless:
            continue
        item = open_idless.pop()
        if not _AGY_RESULT_RE.match(str(step.get("content") or "")):
            mark([item])
        elif open_ids:
            # An explicit call is still open: the result may be its own.
            tentative.append((item, step, set(open_ids)))
        else:
            positional[id(item)] = step
    mark(open_idless + group)
    for item, step, depends in tentative:
        settled = all(dep in outputs and dep not in duplicate_ids for dep in depends)
        positional[id(item)] = step if settled else {"_capture_ambiguous": True}

    position = {id(step): index for index, step in enumerate(steps or [])}
    pairs = []
    ambiguous = False
    for item in calls:
        cid = _call_id(item)
        if cid:
            result = outputs.get(cid) if cid not in duplicate_ids else None
            if result is None:
                result = {"_capture_ambiguous": True}
        else:
            result = positional.get(id(item)) or {"_capture_ambiguous": True}
        background = _BACKGROUND_RE.search(str(result.get("content") or ""))
        if background and id(result) in position:
            result = _background_result(steps, position[id(result)], background.group(1))
        ambiguous = ambiguous or bool(result.get("_capture_ambiguous"))
        if _is_echo_command(_cmd_text(item)):
            continue
        pairs.append((item, str(result.get("content") or ""), result))
    return pairs, ambiguous


INSPECTION_RE = re.compile(r"^(?:rg|grep|egrep|fgrep|cat|sed|awk|head|tail|less|more|ls|stat|wc|find)$", re.I)


def _stages(command):
    """Shell stages of a command line; pipes, lists and newlines split them."""
    return [part.strip() for part in re.split(r"\|\|?|&&|;|\n", str(command or "")) if part.strip()]


def _program(stage):
    words = re.sub(r"^(?:\w+=\S*\s+)+", "", stage).split()
    return words[0].rsplit("/", 1)[-1] if words else ""


MUTATING_TOOL_NAMES = {
    "run_command", "bash", "exec", "terminal", "cmd", "command",
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "edit_file", "create_file", "apply_diff", "patch", "modify_file",
    "write_file", "write", "edit", "multiedit", "notebook_edit", "notebookedit",
}


# A script run: its output is the script's own, so the gate cannot classify it.
SCRIPT_RE = re.compile(
    r"(?:^|[\s;&|(\x22'])(?:(?:ba|z)?sh\s+\S+\.sh|\./\S+\.sh|python3?\s+(?!-c\b)\S+\.py|"
    r"node\s+(?!-e\b)\S+\.(?:c|m)?js)", re.I)
_SHELL_TOOL_NAMES = {"run_command", "bash", "exec", "terminal", "cmd", "command", "manage_task", ""}


def is_execution(call) -> bool:
    """A shell run or a background-task status read; timers and file tools are not."""
    return str(call.get("name") or "").strip().lower() in _SHELL_TOOL_NAMES


def has_execution_or_edit(steps) -> bool:
    for step in steps or []:
        calls = step.get("tool_calls") if isinstance(step, dict) else None
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict) and str(call.get("name") or "").strip().lower() in MUTATING_TOOL_NAMES:
                return True
    return False
