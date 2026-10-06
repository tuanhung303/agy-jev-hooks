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


def status_record(output):
    body = status_body(output)
    if not body:
        return {}
    try:
        value = json.loads(body)
        if isinstance(value, dict):
            return value
    except (json.JSONDecodeError, TypeError):
        pass
    # Multiple JSON records are not one record. Keeping them separate avoids
    # borrowing an ID from one line and a terminal status from another.
    records = []
    for line in body.splitlines():
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            records = []
            break
        if isinstance(value, dict):
            records.append(value)
        else:
            records = []
            break
    if len(records) == 1:
        return records[0]
    if len(records) > 1:
        return {}
    try:
        value = ast.literal_eval(body)
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return value
    except (SyntaxError, ValueError):
        pass
    return {}


def terminal_status(output):
    value = status_record(output)
    if isinstance(value.get("status"), str):
        return value["status"].strip().lower()
    body = status_body(output)
    if _multiple_json_records(body):
        return ""
    match = re.search(r"\bstatus\b[\"']?\s*[:=]\s*[\"']?([\w-]+)[\"']?", body, re.I)
    return match.group(1).strip().lower() if match else ""


def run_id(output):
    value = status_record(output)
    for key in ("run_id", "execution_id", "executionArn", "id"):
        if value.get(key) is not None:
            return str(value[key]).strip()
    body = status_body(output)
    if _multiple_json_records(body):
        return ""
    match = re.search(
        r"[\"']?\b(?:run[_ -]?id|execution[_ -]?id|executionarn|id)\b[\"']?\s*"
        r"[:=]\s*[\"']?([\w./:-]+)", body, re.I)
    return match.group(1).strip() if match else ""


def _multiple_json_records(body):
    records = []
    for line in str(body or "").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(value, dict):
            return False
        records.append(value)
    return len(records) > 1


def count_observation(output):
    """Return labelled count observations and an explicit difference value."""
    text = clean(output)
    values = {}
    difference = None
    # Structured lists keep field identity inside each record.
    body = status_body(text)
    try:
        structured = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        structured = None
    records = structured if isinstance(structured, list) else [structured]
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            label = {"sourcecount": "source", "targetcount": "target",
                     "rowcount": "row", "recordcount": "record",
                     "datacount": "data"}.get(normalized)
            if label:
                try:
                    values[label] = int(value)
                except (TypeError, ValueError):
                    pass
    for match in re.finditer(r"\b(source|target|row|record|data)[_\s-]*count\s*[:=|]\s*(\d+)", text, re.I):
        values[match.group(1).lower()] = int(match.group(2))
    diff = re.search(r"\b(?:difference|diff|mismatch(?:_count)?)\s*[:=|]\s*(\d+)", text, re.I)
    if diff:
        difference = int(diff.group(1))
    # sqlcmd tables can include columns that are not counts. Map every cell to
    # its header position, then select only labelled count columns.
    lines = body.splitlines()
    for index, line in enumerate(lines[:-1]):
        headers = re.findall(r"\S+", line)
        labels = [re.sub(r"[^a-z]", "", h.lower()) for h in headers]
        if not any(re.fullmatch(r"(?:source|target|row|record|data)count", h) for h in labels):
            continue
        cursor = index + 1
        while cursor < len(lines) and (not lines[cursor].strip() or
                re.fullmatch(r"[\s|+:-]+", lines[cursor])):
            cursor += 1
        row = lines[cursor] if cursor < len(lines) else ""
        cells = re.findall(r"\S+", row)
        if len(cells) >= len(headers) and not re.search(r"rows? affected|rows? returned", row, re.I):
            for label, cell in zip(labels, cells):
                if not re.fullmatch(r"(?:source|target|row|record|data)count", label):
                    continue
                match = re.fullmatch(r"-?\d+", cell.strip("|"))
                if match:
                    key = re.sub(r"count$", "", label)
                    values[key] = int(match.group(0))
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
    return values, difference


def count_scalar_values(output):
    values = []
    for line in status_body(output).splitlines():
        stripped = line.strip()
        if not stripped or re.search(r"rows? (?:affected|returned)|^\(.*\)$", stripped, re.I):
            continue
        if re.fullmatch(r"\|?\s*\d+\s*\|?", stripped):
            values.append(int(re.search(r"\d+", stripped).group(0)))
    return values
