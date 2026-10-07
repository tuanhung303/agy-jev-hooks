"""Parse native and structured run/count receipts used by claim checks."""
import ast
import json
import re

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def clean(output):
    return _ANSI_RE.sub("", str(output or ""))


def status_body(output):
    text = clean(output)
    lines = [line for line in text.splitlines()
             if not re.match(r"^\s*(?:Created At:|Completed At:|Output:|Stdout:|Stderr:|"
                             r"The command exited with code \d+|exit[=: ]+\d+)\s*", line, re.I)]
    return "\n".join(lines).strip()


def _parse_record(text):
    """One dict or a list of dicts from a JSON or Python literal, else None."""
    for parse in (json.loads, ast.literal_eval):
        try:
            value = parse(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            return value
    return None


def records(output):
    """Structured records, each kept whole; log lines between them are skipped.

    Returns None when the body has no structured record, so callers can fall
    back to a single plain-text record.
    """
    body = status_body(output)
    if not body:
        return None
    whole = _parse_record(body)
    if whole is not None:
        return whole
    found = []
    for line in body.splitlines():
        parsed = _parse_record(line.strip()) if line.strip()[:1] in "{[" else None
        if parsed is not None:
            found.extend(parsed)
    return found or None


_STATUS_TEXT_RE = re.compile(r"\bstatus\b[\"']?\s*[:=]\s*[\"']?([\w-]+)", re.I)
_ID_TEXT_RE = re.compile(
    r"[\"']?\b(?:run[_ -]?id|execution[_ -]?id|executionarn|id)\b[\"']?\s*[:=]\s*[\"']?([\w./:-]+)", re.I)
_ID_KEYS = ("runid", "executionid", "executionarn", "id")
_NAME_KEYS = ("pipelinename", "jobname", "workflowname", "name")


def _text_record(output):
    """A plain-text body is one record only when it names one status and at most one ID."""
    body = status_body(output)
    statuses, ids = _STATUS_TEXT_RE.findall(body), _ID_TEXT_RE.findall(body)
    if len(statuses) > 1 or len(ids) > 1:
        return None
    record = {}
    if statuses:
        record["status"] = statuses[0]
    if ids:
        record["id"] = ids[0]
    return record


def status_record(output):
    """The single record of a receipt, or {} when there is not exactly one."""
    found = records(output)
    if found is None:
        return _text_record(output) or {}
    return found[0] if len(found) == 1 else {}


def _keyed(record, keys):
    """First value under any of keys, matched case-insensitively without `_`."""
    folded = {re.sub(r"[_\s-]", "", str(k)).lower(): v for k, v in record.items()}
    for key in keys:
        if folded.get(key) is not None:
            return str(folded[key]).strip()
    return ""


def _record_id(record):
    return _keyed(record, _ID_KEYS)


def _names_run(record, wanted):
    wanted = wanted.casefold()
    return _record_id(record).casefold() == wanted or _keyed(record, _NAME_KEYS).casefold() == wanted


def _record_status(record):
    value = record.get("status")
    return value.strip().lower() if isinstance(value, str) else ""


def terminal_status(output):
    return _record_status(status_record(output))


def run_id(output):
    return _record_id(status_record(output))


_TERMINAL_SUCCESS = {"succeeded", "success", "completed", "complete"}
_TERMINAL_FAILURE = {"failed", "failure", "error", "cancelled", "canceled", "aborted"}
_NOT_FINISHED = {"inprogress", "in_progress", "queued", "running", "notstarted", "pending"}


def run_observation(output, wanted_id=""):
    """Outcome of the claimed run from records that carry both ID and status.

    "success" or "failure" when the claimed run's own records agree;
    "unknown" when its records lack a terminal status, disagree, or cannot be
    associated with one run; "absent" when no record names a run.
    """
    found = records(output)
    if found is None:
        single = _text_record(output)
        found = [single] if single is not None else None
    if found is None:
        return "unknown"
    with_id = [r for r in found if _record_id(r) or _keyed(r, _NAME_KEYS)]
    if not with_id:
        # A status read that names no run cannot be tied to the claim.
        return "unknown" if any(_record_status(r) for r in found) else "absent"
    if wanted_id:
        mine = [r for r in with_id if _names_run(r, wanted_id)]
    elif len(with_id) == 1:
        mine = with_id
    else:
        statuses = {_record_status(r) for r in with_id}
        if statuses <= _TERMINAL_SUCCESS:
            return "success"
        return "unknown"
    if not mine:
        return "absent"
    statuses = {_record_status(r) for r in mine}
    failed, passed = statuses & _TERMINAL_FAILURE, statuses & _TERMINAL_SUCCESS
    if (failed or statuses & _NOT_FINISHED) and not passed:
        # A run still in progress contradicts a completion claim.
        return "failure"
    if passed and not failed and not (statuses - _TERMINAL_SUCCESS - {""}):
        return "success"
    return "unknown"


_COUNT_KEYS = {"sourcecount": "source", "targetcount": "target", "rowcount": "row",
               "recordcount": "record", "datacount": "data"}


def count_records(output):
    """Labelled count records, each from one record, and an explicit difference.

    Structured records and table rows stay separate. Plain-text labels form
    one record; a label that repeats with different values marks the text as
    unassociated (None in place of the records list).
    """
    text = clean(output)
    body = status_body(text)
    out = []
    difference = None
    for record in records(text) or []:
        labels = {}
        for key, value in record.items():
            label = _COUNT_KEYS.get(re.sub(r"[^a-z]", "", str(key).lower()))
            if label:
                try:
                    labels[label] = int(value)
                except (TypeError, ValueError):
                    pass
        if labels:
            out.append(labels)
    seen = {}
    for match in re.finditer(r"\b(source|target|row|record|data)[_\s-]*count\s*[:=|]\s*(\d+)", text, re.I):
        seen.setdefault(match.group(1).lower(), set()).add(int(match.group(2)))
    if seen:
        if any(len(values) > 1 for values in seen.values()):
            return None, difference
        out.append({label: values.pop() for label, values in seen.items()})
    diff = re.search(r"\b(?:difference|diff|mismatch(?:_count)?)\s*[:=|]\s*(\d+)", text, re.I)
    if diff:
        difference = int(diff.group(1))
    # sqlcmd tables can include columns that are not counts. Map every cell to
    # its header position, then select only labelled count columns.
    lines = body.splitlines()
    for index, line in enumerate(lines[:-1]):
        headers = re.findall(r"\S+", line)
        labels = [re.sub(r"[^a-z]", "", h.lower()) for h in headers]
        if not any(label in _COUNT_KEYS for label in labels):
            continue
        cursor = index + 1
        while cursor < len(lines) and (not lines[cursor].strip() or
                re.fullmatch(r"[\s|+:-]+", lines[cursor])):
            cursor += 1
        # Every data row is its own record, up to a blank line or footer.
        for row in lines[cursor:]:
            if not row.strip() or re.search(r"rows? affected|rows? returned", row, re.I):
                break
            cells = re.findall(r"\S+", row)
            if len(cells) < len(headers):
                break
            row_labels = {}
            for label, cell in zip(labels, cells):
                match = re.fullmatch(r"-?\d+", cell.strip("|"))
                if label in _COUNT_KEYS and match:
                    row_labels[_COUNT_KEYS[label]] = int(match.group(0))
            if row_labels:
                out.append(row_labels)
    # A one-column difference table is meaningful only when it has one
    # labelled difference column and a single numeric value below it.
    for index, line in enumerate(lines[:-1]):
        if not re.fullmatch(r"\s*(?:difference|diff|mismatch(?:_count)?)\s*", line, re.I):
            continue
        for candidate in lines[index + 1:]:
            candidate = candidate.strip()
            if not candidate or re.fullmatch(r"[\s|+:-]+", candidate):
                continue
            match = re.fullmatch(r"\|?\s*(\d+)\s*\|?", candidate)
            if match:
                difference = int(match.group(1))
            break
    return out, difference


def count_observation(output):
    """The single labelled count record and difference ({} unless exactly one record)."""
    found, difference = count_records(output)
    return (found[0] if found and len(found) == 1 else {}), difference


def count_scalar_values(output):
    values = []
    for line in status_body(output).splitlines():
        stripped = line.strip()
        if not stripped or re.search(r"rows? (?:affected|returned)|^\(.*\)$", stripped, re.I):
            continue
        if re.fullmatch(r"\|?\s*\d+\s*\|?", stripped):
            values.append(int(re.search(r"\d+", stripped).group(0)))
    return values
