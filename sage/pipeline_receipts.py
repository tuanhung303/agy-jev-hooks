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
    match = re.search(r"\bstatus\b[\"']?\s*[:=]\s*[\"']?([\w-]+)[\"']?",
                      status_body(output), re.I)
    return match.group(1).strip().lower() if match else ""


def run_id(output):
    value = status_record(output)
    for key in ("run_id", "execution_id", "executionArn", "id"):
        if value.get(key) is not None:
            return str(value[key]).strip()
    match = re.search(
        r"[\"']?\b(?:run[_ -]?id|execution[_ -]?id|executionarn|id)\b[\"']?\s*"
        r"[:=]\s*[\"']?([\w./:-]+)", status_body(output), re.I)
    return match.group(1).strip() if match else ""


def count_observation(output):
    """Return labelled count observations and an explicit difference value."""
    text = clean(output)
    values = {}
    difference = None
    for match in re.finditer(r"\b(source|target|row|record|data)[_\s-]*count\s*[:=|]\s*(\d+)", text, re.I):
        values[match.group(1).lower()] = int(match.group(2))
    diff = re.search(r"\b(?:difference|diff|mismatch(?:_count)?)\s*[:=|]\s*(\d+)", text, re.I)
    if diff:
        difference = int(diff.group(1))
    lines = status_body(text).splitlines()
    for index, line in enumerate(lines[:-1]):
        labels = re.findall(r"\b(source|target)[_\s-]*count\b", line, re.I)
        if len(labels) < 2:
            continue
        cursor = index + 1
        while cursor < len(lines) and (not lines[cursor].strip() or
                re.fullmatch(r"[\s|+:-]+", lines[cursor])):
            cursor += 1
        row = lines[cursor] if cursor < len(lines) else ""
        numbers = re.findall(r"(?<![\w.])-?\d+(?![\w.])", row)
        if len(numbers) >= len(labels) and not re.search(r"rows? affected|rows? returned", row, re.I):
            values.update({label.lower(): int(number) for label, number in zip(labels, numbers)})
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
