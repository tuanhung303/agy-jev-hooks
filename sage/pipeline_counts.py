"""Count-query receipts for data reconciliation claims."""
import re

from sage.pipeline_receipts import (
    clean as _clean, count_records as _count_records, count_scalar_values as _count_scalar_values,
)

DATA_CLAIM_RE = re.compile(
    r"\b(?:row|record|data)\s+counts?\s+(?:match(?:ed)?|reconciled)\b|"
    r"\b(?:row|record|data)\s+(?:are\s+)?reconciled\b|"
    r"\b(?:reconciled|reconcile[d]?)\s+(?:row|record|data)\s+counts?\b", re.I)


def _status_ok(output, result_step=None):
    from sage.pipeline_claims import _status_ok as status_ok
    return status_ok(output, result_step)


def sql_content_for(command, pairs, command_text):
    path = re.search(r"\bsqlcmd\s+-i\s+([^\s]+)", command, re.I)
    if not path:
        return ""
    wanted = path.group(1).strip("\"'").replace("\\", "/").rsplit("/", 1)[-1]
    for call, output, _result in pairs:
        if str(call.get("name") or "").lower() not in {"read_file", "readfile", "view_file"}:
            continue
        source_path = command_text(call).replace("\\", "/").strip("\"'")
        source = source_path.rsplit("/", 1)[-1]
        if source_path == path.group(1).strip("\"'").replace("\\", "/") or (
                "/" not in source_path and source == wanted):
            return _clean(output)
    return ""


def is_count_query(call, pairs, command_text):
    command = command_text(call)
    if re.search(r"\bselect\b[\s\S]*\bcount\s*\(", command, re.I):
        return True
    content = sql_content_for(command, pairs, command_text)
    return bool(content and re.search(r"\bselect\b[\s\S]*\bcount\s*\(", content, re.I))


def counts_cover(sentence, pairs, command_text):
    claim_match = DATA_CLAIM_RE.search(sentence)
    tail = sentence[claim_match.end():] if claim_match else ""
    tail = re.sub(r"(?<=\d),(?=\d{3}\b)", "", tail)  # thousands separators: 10,452 is one count
    expected_match = re.search(r"\s*[:=]?\s*(\d+)\s*(?:and|,|vs\.?)\s*(\d+)", tail, re.I)
    expected = tuple(map(int, expected_match.groups())) if expected_match else None
    if expected and expected[0] != expected[1]:
        return False
    observed = []
    zero_difference = False
    count_query_seen = False
    usable_observation = False
    unassociated = False
    for call, output, result_step in pairs:
        command = command_text(call)
        if not is_count_query(call, pairs, command_text):
            continue
        if not _status_ok(output, result_step):
            # A failed query observes nothing; a later successful retry decides.
            continue
        count_query_seen = True
        found, difference = _count_records(output)
        if found is None:
            unassociated = True
            continue
        usable_observation = usable_observation or bool(found or difference is not None)
        if difference == 0:
            zero_difference = True
        elif difference is not None:
            return False
        complete = [r for r in found if ("source" in r and "target" in r)
                    or ("row" in r and "record" in r)]
        for record in complete:
            left, right = (record["source"], record["target"]) if "source" in record \
                else (record["row"], record["record"])
            if left != right:
                return False
            observed.append((left, right))
        partial = [r for r in found if r not in complete]
        if len(found) > 1 and partial:
            # Several records, some one-sided: which sides compare is unknown.
            unassociated = True
            continue
        for record in partial:
            if "source" in record:
                observed.append((record["source"], None))
            elif "target" in record:
                observed.append((None, record["target"]))
        if not found and "source" in command.lower():
            vals = _count_scalar_values(output)
            if vals:
                usable_observation = True
                observed.append((int(vals[0]), None))
        elif not found and "target" in command.lower():
            vals = _count_scalar_values(output)
            if vals:
                usable_observation = True
                observed.append((None, int(vals[0])))
    if unassociated:
        return None
    sources = [source for source, _ in observed if source is not None]
    targets = [target for _, target in observed if target is not None]
    pair = (sources[0], targets[0]) if sources and targets else None
    consistent = bool(pair and len(set(sources)) == 1 and len(set(targets)) == 1
                      and pair[0] == pair[1] and (expected is None or pair == expected))
    rows_equal = bool(observed) and expected is None and all(
        left is not None and left == right for left, right in observed)
    if consistent or rows_equal or bool(zero_difference and (expected is None or expected[0] == expected[1])):
        return True
    if count_query_seen and not usable_observation:
        return None
    if count_query_seen and (not sources or not targets):
        # One side was counted: it can still contradict the stated count.
        if expected and (any(s != expected[0] for s in sources) or any(t != expected[1] for t in targets)):
            return False
        return None
    return False
