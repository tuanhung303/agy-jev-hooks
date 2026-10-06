"""Bind claim checks to captured tool executions and their outputs."""
import ast
import re
import shlex
from typing import Any, Dict, List, Tuple

_ECHO_RE = re.compile(r"^\s*(?:echo|printf)\b", re.I)
_AGY_RESULT_RE = re.compile(r"^\s*Created At:", re.I)


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


def _call_output_pairs(steps: List[Dict[str, Any]]) -> Tuple[List[Tuple[Dict[str, Any], str, Dict[str, Any]]], bool]:
    outputs = {}
    calls = []
    positional = {}
    duplicate_output_ids = set()
    call_ids = set()
    duplicate_call_ids = set()
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        step_type = str(step.get("type") or "").upper()
        meta = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
        oid = step.get("tool_call_id") or step.get("call_id") or meta.get("tool_call_id") or meta.get("call_id")
        if oid:
            if str(oid) in outputs:
                duplicate_output_ids.add(str(oid))
            outputs[str(oid)] = step
        tool_calls = step.get("tool_calls")
        for call in tool_calls if isinstance(tool_calls, list) else []:
            if not isinstance(call, dict):
                continue
            cid = call.get("id") or call.get("tool_call_id") or call.get("call_id")
            if cid and str(cid) in call_ids:
                duplicate_call_ids.add(str(cid))
            elif cid:
                call_ids.add(str(cid))
            if not _is_echo_command(_cmd_text(call)):
                calls.append(call)
    ambiguous = bool(duplicate_output_ids or duplicate_call_ids)
    ambiguous_ids = duplicate_output_ids | duplicate_call_ids
    # Bind only within a native serial window: one ID-less call followed by
    # one native result before the next planner response or user turn. If a
    # later planner starts while an earlier ID-less call is pending, their
    # windows overlap and positional binding is unsafe.
    pending = []
    overlap_active = False
    for idx, step in enumerate(steps or []):
        stype = str(step.get("type") or "").upper() if isinstance(step, dict) else ""
        if stype in {"PLANNER_RESPONSE", "USER_INPUT"}:
            if stype == "USER_INPUT":
                overlap_active = False
            if pending:
                overlap_active = True
                ambiguous = True
                for call in pending:
                    positional[id(call)] = {"_capture_ambiguous": True}
                pending = []
            calls_in_step = step.get("tool_calls") if isinstance(step, dict) else None
            batch = [c for c in calls_in_step if isinstance(c, dict) and not (
                c.get("id") or c.get("tool_call_id") or c.get("call_id"))] \
                if isinstance(calls_in_step, list) else []
            if len(batch) == 1 and not overlap_active:
                pending = batch
            elif len(batch) > 1:
                ambiguous = True
                for call in batch:
                    positional[id(call)] = {"_capture_ambiguous": True}
                pending = []
            elif batch and overlap_active:
                ambiguous = True
                for call in batch:
                    positional[id(call)] = {"_capture_ambiguous": True}
                pending = []
        elif stype == "GENERIC" and pending:
            content = str(step.get("content") or "")
            if _AGY_RESULT_RE.match(content):
                positional[id(pending[0])] = step
                pending = []
            else:
                ambiguous = True
                positional[id(pending[0])] = {"_capture_ambiguous": True}
                pending = []
    for call in pending:
        positional[id(call)] = {"_capture_ambiguous": True}
    pairs = []
    for call in calls:
        cid = call.get("id") or call.get("tool_call_id") or call.get("call_id")
        result = outputs.get(str(cid)) if cid else positional.get(id(call))
        if cid and str(cid) in ambiguous_ids:
            result = None
        if result is None and cid:
            result = {"_capture_ambiguous": True}
        pairs.append((call, str((result or {}).get("content") or ""), result or {}))
    return pairs, ambiguous
