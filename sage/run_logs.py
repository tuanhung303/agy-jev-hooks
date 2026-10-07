"""Receipts that tools print about their own work.

A polling script's status lines, an object-store transfer log, a monitor
subagent's report and a dbt run_results read each observe an operation the
claim names. They are narrow formats: anything looser stays unknown.
"""
import re

_POLL_HEADER_RE = re.compile(r"\bPolling\b[^\n]*?\bstatus\b[^\n]*?\bfor\s+([\w.:-]{6,}?)\.{0,3}\s*$", re.I | re.M)
_POLL_STATUS_RE = re.compile(r"^\s*(?:Attempt\s+\d+\s+)?status\s*=\s*([\w-]+)\s*$", re.I | re.M)


def poll_records(text):
    """One run record from a polling log: the polled ID and its last status line."""
    header = _POLL_HEADER_RE.search(str(text or ""))
    if not header:
        return []
    statuses = _POLL_STATUS_RE.findall(str(text)[header.end():])
    return [{"id": header.group(1), "status": statuses[-1]}] if statuses else []


_FINISHED_RE = re.compile(r"Task id \"?[^\s\"]+\"? finished with result:?(.*)", re.S)


def finished_outputs(steps):
    """Bodies of background-task completion records the system delivered."""
    bodies = []
    for step in steps or []:
        if str(step.get("type") or "") != "SYSTEM_MESSAGE":
            continue
        match = _FINISHED_RE.search(str(step.get("content") or ""))
        if match:
            bodies.append(match.group(1).split("</SYSTEM_MESSAGE>")[0])
    return bodies


# gcloud storage / gsutil print one line per object they wrote to the bucket.
_TRANSFER_RE = re.compile(
    r"^\s*(?:Copying\s+\S+\s+to|upload:\s+\S+\s+to)\s+((?:gs|s3|az|abfss?|wasbs?)://\S+)", re.I | re.M)


def transfer_targets(text):
    """Destination URIs that an object-store transfer log names."""
    return _TRANSFER_RE.findall(str(text or ""))


# agy delivers a subagent's report as a SYSTEM_MESSAGE from another session;
# a background task's finish comes from `<session>/task-N` instead.
_SUBAGENT_RE = re.compile(r"\[Message\][^\n]*\bsender=(?![^\s]*/task-)[\w-]+[^\n]*\bcontent=", re.I)
_REPORTED_STATE_RE = re.compile(
    r"\b(?:succeeded|success(?:fully)?|completed|finished|failed|failure)\b", re.I)
_IDENT_RE = re.compile(r"\b[A-Za-z][\w-]*_[\w-]+\b")


def _names(sentence):
    """Identifiers in a claim, plus the tail after a `prefix__` namespace."""
    found = set()
    for ident in _IDENT_RE.findall(str(sentence or "")):
        found.add(ident.lower())
        if "__" in ident:
            found.add(ident.split("__", 1)[1].lower())
    return {name for name in found if len(name) >= 4}


def subagent_reported(sentence, steps):
    """A monitor subagent reported a terminal state for a run this claim names."""
    names = _names(sentence)
    if not names:
        return False
    for step in steps or []:
        if str(step.get("type") or "") != "SYSTEM_MESSAGE":
            continue
        text = str(step.get("content") or "")
        match = _SUBAGENT_RE.search(text)
        if not match:
            continue
        body = text[match.end():].lower()
        if _REPORTED_STATE_RE.search(body) and any(name in body for name in names):
            return True
    return False


_RUN_RESULTS_STATUS_RE = re.compile(r"Status counts:\s*\{([^}]*)\}", re.I)


def dbt_results_passed(command, output):
    """A read of dbt run_results.json whose status counts hold no error or failure."""
    if "run_results" not in str(command or ""):
        return False
    text = str(output or "")
    counts = _RUN_RESULTS_STATUS_RE.search(text)
    failures = re.search(r"^\s*Failures:\s*(\d+)\s*$", text, re.M)
    if not counts or not failures or int(failures.group(1)) != 0:
        return False
    keys = {k.strip(" '\"").lower() for k in re.findall(r"['\"]?(\w+)['\"]?\s*:", counts.group(1))}
    return bool(keys) and not keys & {"error", "fail", "runtime error"}
