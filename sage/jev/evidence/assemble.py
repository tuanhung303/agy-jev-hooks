"""sage.jev.evidence.assemble - Evidence assembly for the Sage Compass router.

Blocks budgeted by artifact. Safety: complete-field redaction (oversized
fields omitted, never sliced), identity-bound receipts, structured exit status
over textual receipts, screenshot OCR in `visual_text`, and `=== header ===`
lines evidence cannot forge.
"""
import os
import tempfile
from typing import Any, Dict, List, Tuple

from sage.jev.evidence.attribution import (
    _bind_result_step,
    _first_str,
    _is_result_step,
    _result_id,
    _status_of,
    _step_tools,
)
from sage.jev.evidence.blast import detect_blast_radius_gap
from sage.jev.evidence.tools import (
    READ_TOOLS,
    extract_command_from_args,
    extract_path_from_args,
    normalize_tool_args,
)
from sage.jev.evidence.context import _collect_requirements
from sage.imgtext import symptom_flags, visual_text_and_flags
from sage.jev.transport import _bound
from sage.jev.evidence.redact import REDACT_TIME_BUDGET_S, _RedactBudget, _redact_field

WRITE_TOOLS = {
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "edit_file", "create_file", "apply_diff", "patch", "modify_file", "write_file",
    "write", "edit", "multiedit", "notebook_edit", "notebookedit",
}
FULL_WRITE_TOOLS = {"write_to_file", "create_file", "write_file", "write"}
RUN_TOOLS = {"run_command", "bash", "exec", "terminal"}
CONTENT_ARG_KEYS = ("Content", "content", "NewString", "new_string", "Replace", "ReplacementContent", "Text", "Code")

BLOCK_BUDGETS = {
    "user_requirements": 6000,
    "turn_history": 3000,
    "artifact_diffs": 14000,
    "command_receipts": 10000,
    "visual_text": 2000,
    "final_reply": 5000,
}
PAYLOAD_CHAR_CAP = 44000
RECEIPT_TAIL_CHARS = 300      # older receipts: tail only
RECEIPT_FULL_CHARS = 2000     # most recent receipt: kept whole up to this cap
PER_FILE_DIFF_CHARS = 3500    # most recently written file
ARTIFACT_OLDER_CHARS = 1750   # previously written files
MAX_STEPS = 400
MAX_INGEST_BYTES = 16 * 1024 * 1024


class _BoundedSteps(list):
    """List-compatible parsed steps carrying ingestion omissions separately."""

    def __init__(self, steps: List[Dict[str, Any]], omitted_bytes: int):
        super().__init__(steps)
        self.omitted_bytes = omitted_bytes


def _read_steps_bounded(path: str) -> List[Dict[str, Any]]:
    """Parse only a bounded snapshot; preparation failures propagate to abstention."""
    from sage.transcript import _read_transcript_steps
    with open(path, "rb") as handle:
        omitted = max(0, os.fstat(handle.fileno()).st_size - MAX_INGEST_BYTES)
        handle.seek(max(0, omitted - 1))
        data = handle.read(MAX_INGEST_BYTES + bool(omitted))
    if omitted:
        preceding, data = data[:1], data[1:]
        if preceding != b"\n":
            end = data.find(b"\n") + 1 or len(data)
            omitted += end
            data = data[end:]
    with tempfile.NamedTemporaryFile("wb", suffix=".jsonl") as tmp:
        tmp.write(data)
        tmp.flush()
        parsed = [s for s in _read_transcript_steps(tmp.name) if isinstance(s, dict)]
    return _BoundedSteps(parsed, omitted)


def _tail_lines(body: str, cap: int) -> str:
    """Bound a body to the cap at a line boundary; a partial first line is
    dropped and the omission is marked, so a receipt can never end mid-token."""
    tail = body[-cap:]
    cut = tail.find("\n")
    if cut != -1:
        tail = tail[cut + 1:]
    marker = f"[... {len(body) - len(tail)} chars omitted ...]"
    room = max(0, cap - len(marker) - 1)
    if len(tail) > room:
        tail = tail[len(tail) - room:]
        cut = tail.find("\n")
        if cut != -1:
            tail = tail[cut + 1:]
    return f"{marker}\n{tail}"


def assemble_evidence(steps: List[Dict[str, Any]]) -> Dict[str, str]:
    """Build the five delimited evidence blocks with identity-bound receipts."""
    omitted_bytes = getattr(steps, "omitted_bytes", 0)
    full_steps = [s for s in steps if isinstance(s, dict)]
    steps = full_steps[-MAX_STEPS:]
    omission = f"[older transcript omitted: {len(full_steps) - len(steps)} steps]" \
        if len(full_steps) > len(steps) else ""
    if omitted_bytes:
        omission = f"[older transcript omitted: {omitted_bytes} bytes]\n" + omission
    budget = _RedactBudget(REDACT_TIME_BUDGET_S)
    output_ids: Dict[str, Dict[str, Any]] = {}
    duplicate_ids: set = set()
    for step in steps:
        if not _is_result_step(step):
            continue
        oid = _result_id(step)
        if not oid:
            continue
        if oid in output_ids:
            duplicate_ids.add(oid)
        output_ids[oid] = step
    turn_lines: List[str] = []
    diffs: Dict[str, Dict[str, Any]] = {}
    receipts: List[Tuple[str, str]] = []
    written: set = set()
    inspected: set = set()
    executed: List[str] = []
    final_reply = ""
    turn_no = 0
    for idx, step in enumerate(steps):
        if step.get("type") == "USER_INPUT" and str(step.get("content") or "").strip():
            turn_no += 1
            turn_lines.append(f"T-{turn_no}: user request")
        if step.get("type") != "PLANNER_RESPONSE":
            continue
        if str(step.get("content") or "").strip():
            final_reply = str(step["content"]).strip()
        calls = _step_tools(step)
        if not calls:
            continue
        window: List[Dict[str, Any]] = []
        for nxt in steps[idx + 1:]:
            if nxt.get("type") in ("PLANNER_RESPONSE", "USER_INPUT"):
                break
            window.append(nxt)
        window_results = [w for w in window if _is_result_step(w)]
        for t_idx, call in enumerate(calls):
            result_step, raw_output, ambiguous = _bind_result_step(
                call, t_idx, calls, window_results, output_ids, duplicate_ids,
            )
            name = str(call.get("name") or "").strip().lower()
            args = normalize_tool_args(call.get("args") or call.get("arguments") or {}, name)
            if name in WRITE_TOOLS:
                raw_target = extract_path_from_args(args)
                if raw_target:
                    written.add(raw_target)
                target = _redact_field(raw_target, "path", budget)
                if not target:
                    continue
                state = diffs.setdefault(
                    target,
                    {"attempts": 0, "content": "<content not captured>", "outcome": "unknown",
                     "order": 0, "full_write": False, "stale": False},
                )
                state["attempts"] += 1
                state["order"] = idx
                present = _first_str(args, CONTENT_ARG_KEYS)
                if name in FULL_WRITE_TOOLS:
                    state["full_write"] = True
                    if present is not None:
                        state["content"] = present
                    state["outcome"] = "unknown"
                else:
                    # Patch-family attempts never overwrite the captured file
                    # content: the resulting file is unknown until re-read.
                    state["stale"] = True
                    state["outcome"] = "unknown"
                    if present:
                        state["patch_note"] = _bound(_redact_field(present, "patch", budget), 200)
                if raw_output is None or ambiguous:
                    state["outcome"] = "unknown"
                else:
                    status = _status_of(result_step or {}, raw_output)
                    failed = status not in ("0", "unknown")
                    state["outcome"] = f"error(exit={status})" if failed else (
                        "success" if status != "unknown" else "unknown"
                    )
                    state["receipt"] = _bound(_redact_field(raw_output, "write receipt", budget), 200)
            elif name in RUN_TOOLS:
                raw_command = extract_command_from_args(args)
                if raw_command:
                    executed.append(raw_command)
                command = _redact_field(raw_command, "command", budget)
                if not command:
                    continue
                if result_step is not None:
                    status = _status_of(result_step, str(raw_output or ""))
                    body = _redact_field(str(raw_output or ""), "command output", budget)
                    receipts.append((f"$ {command}\n[exit={status}]", body))
                else:
                    receipts.append((f"$ {command}\n", "<no unambiguous output captured>"))
            elif name in READ_TOOLS:
                inspected_path = extract_path_from_args(args)
                if inspected_path:
                    inspected.add(inspected_path)
    rendered_receipts = []
    for pos, (header, body) in enumerate(receipts):
        cap = RECEIPT_FULL_CHARS if pos == len(receipts) - 1 else RECEIPT_TAIL_CHARS
        rendered_receipts.append(header + "\n" + (body if len(body) <= cap else _tail_lines(body, cap)))
    # Label-matched evidence for blast_radius_unchecked: deterministic AST
    # consumer gap, bounded and fail-open (no guaranteed workspace root here).
    try:
        diagnostic = detect_blast_radius_gap(written, inspected, executed, workspace_root="") or ""
    except Exception:
        diagnostic = ""
    if diagnostic:
        rendered_receipts.append(f"[blast radius]\n{_bound(diagnostic, 700)}")
    recent_target = max(diffs, key=lambda t: diffs[t].get("order", 0)) if diffs else None
    diff_blocks = []
    for target, state in diffs.items():
        header = f"# {target} (write attempts: {state['attempts']}, latest outcome: {state['outcome']})"
        if state.get("patch_note"):
            header += f" patch: {state['patch_note']}"
        if state.get("receipt"):
            header += f" receipt: {state['receipt']}"
        if state["stale"]:
            header += " [later patch content not captured]"
        if state["content"] == "" and state["full_write"]:
            body = "<file cleared>"
        elif state["content"] == "":
            body = "<partial patch: replacement text not captured>"
        else:
            body = state["content"]
        cap = PER_FILE_DIFF_CHARS if target == recent_target else ARTIFACT_OLDER_CHARS
        diff_blocks.append(header + "\n" + _bound(_redact_field(body, "artifact", budget), cap))
    requirements = _collect_requirements(full_steps, budget)
    if omission:
        requirements = f"{omission}\n{requirements}"
    visual, visual_flags = visual_text_and_flags(full_steps)
    flags = list(dict.fromkeys(
        visual_flags + symptom_flags("\n".join(rendered_receipts) + "\n" + "\n".join(diff_blocks))))
    flag_line = f"[receipt flags: {', '.join(flags)}]" if flags else ""
    visual_block = "\n".join(part for part in (flag_line, visual) if part) or "<no screenshot receipts>"
    blocks = {
        "user_requirements": _bound(requirements, BLOCK_BUDGETS["user_requirements"]),
        "turn_history": "\n".join(turn_lines[-30:]) or "<no turns>",
        "artifact_diffs": "\n\n".join(diff_blocks) or "<no file mutations>",
        "command_receipts": "\n\n".join(rendered_receipts[-12:]) or "<no commands run>",
        "visual_text": _bound(visual_block, BLOCK_BUDGETS["visual_text"]),
        "final_reply": _redact_field(final_reply or "<none>", "final reply", budget),
    }
    return {name: _bound(text, BLOCK_BUDGETS[name]) for name, text in blocks.items()}


def build_payload(blocks: Dict[str, str]) -> str:
    """Line-prefixed serialization: evidence cannot forge a block boundary."""
    parts = []
    for name in ("user_requirements", "turn_history", "artifact_diffs", "command_receipts", "visual_text", "final_reply"):
        lines = [f"| {line}" if line.startswith("=== ") else line
                 for line in blocks.get(name, "").splitlines()]
        parts.append(f"=== {name} ===\n" + "\n".join(lines))
    return _bound("\n\n".join(parts), PAYLOAD_CHAR_CAP)
