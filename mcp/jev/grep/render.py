"""Report assembly and the measured response budget (port of upstream response/render.ts).

A conservative envelope for the mandatory report is reserved before any paid
work; a budget that cannot hold it is refused with RESPONSE_BUDGET_TOO_SMALL.
The complete serialized payload is measured, ranges removed lowest-priority
first until it fits, omission counters recomputed after every removal. Nothing
is ever truncated: no cut JSON, no cut source line, no cut range.
"""
import json
from typing import Callable, List, Optional

from .contracts import (CONTRACT_LIMITS, EXCLUSION_REASONS, SCAN_CAP_KEYS, SCHEMA_VERSION, STOP_REASONS,
                        validate_search_result)
from .tokens import count_reference_tokens, utf8_bytes

_MAX_COUNT = 2 ** 53 - 1


class ResponseBudgetError(Exception):
    def __init__(self, required_tokens: int, requested_tokens: int):
        super().__init__(f"the mandatory report needs {required_tokens} reference tokens "
                         f"but the budget is {requested_tokens}")
        self.required_tokens = required_tokens
        self.requested_tokens = requested_tokens


def serialize_outcome(outcome: dict) -> str:
    return json.dumps(outcome, separators=(",", ":"), ensure_ascii=False)


def mandatory_envelope_tokens(search_id: str, scope: List[str], counter_id: str) -> int:
    """Upper bound for the report every response must carry (widest supported numbers)."""
    worst_case_caps = {key: _MAX_COUNT for key in SCAN_CAP_KEYS}
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "search_id": search_id,
        "status": "partial",
        "excerpts": [],
        "report": {
            "scope": list(scope),
            "inventory_complete": False,
            "scope_fully_scanned": False,
            "files": {
                "discovered": _MAX_COUNT, "eligible": _MAX_COUNT,
                "excluded_by_reason": {reason: _MAX_COUNT for reason in EXCLUSION_REASONS},
                "unreadable": _MAX_COUNT, "changed_before_return": _MAX_COUNT,
            },
            "fragments": {
                "total": _MAX_COUNT, "remote_evaluated": _MAX_COUNT, "cache_reused": _MAX_COUNT,
                "not_evaluated": _MAX_COUNT, "below_threshold": _MAX_COUNT, "above_threshold": _MAX_COUNT,
                "represented_in_response": _MAX_COUNT, "omitted_by_response_budget": _MAX_COUNT,
                "omitted_stale": _MAX_COUNT,
            },
            "selection": {
                "outcome": "no_successful_evaluation", "threshold": 0.123456789,
                "ranges_returned": _MAX_COUNT, "duplicate_ranges_collapsed": _MAX_COUNT,
            },
            "usage": {
                "provider_request_attempts": _MAX_COUNT, "provider_input_tokens_reported": _MAX_COUNT,
                "provider_input_tokens_known_subtotal": _MAX_COUNT, "provider_input_tokens_estimated": _MAX_COUNT,
                "estimated_cost_usd": 1234.567891, "reported_cost_usd": 1234.567891,
                "attempts_with_unknown_usage": _MAX_COUNT, "transmitted_bytes": _MAX_COUNT, "elapsed_ms": _MAX_COUNT,
            },
            "response_budget": {"requested_tokens": _MAX_COUNT, "counter": counter_id,
                                "accounting": "reference_tokenizer"},
            "preflight": {
                "planned_remote_fragments": _MAX_COUNT, "planned_cache_hits": _MAX_COUNT,
                "estimated_first_attempt_tokens": _MAX_COUNT, "estimated_first_attempt_cost_usd": 1234.567891,
                "estimated_first_attempt_requests": _MAX_COUNT,
                "enabled_caps": dict(worst_case_caps), "estimated_required_caps": dict(worst_case_caps),
            },
            "stop_reasons": list(STOP_REASONS),
            "diagnostics_truncated": False,
        },
    }
    return count_reference_tokens(serialize_outcome(envelope))


def excerpt_budget(search_id: str, scope: List[str], counter_id: str, requested_tokens: int) -> int:
    envelope = mandatory_envelope_tokens(search_id, scope, counter_id)
    if envelope >= requested_tokens:
        raise ResponseBudgetError(envelope, requested_tokens)
    return requested_tokens - envelope


def excerpt_cost(range_: dict) -> int:
    """Serialized cost of one excerpt, measured exactly as it will appear."""
    return count_reference_tokens(serialize_outcome({
        "path": range_["path"],
        "start_line": range_["start_line"],
        "end_line": range_["end_line"],
        "file_sha256": range_["sha256"],
        "score": range_["score"],
        "code": range_["text"],
    }) + ",")


def _to_excerpt(range_: dict) -> dict:
    return {
        "path": range_["path"],
        "start_line": range_["start_line"],
        "end_line": range_["end_line"],
        "file_sha256": range_["sha256"],
        "score": range_["score"],
        "code": range_["text"],
    }


def _selection_outcome(inputs: dict, excerpt_count: int, omitted_stale: int) -> str:
    if inputs.get("rejected"):
        return "preflight_rejected"
    if excerpt_count > 0:
        return "selected"
    evaluated = inputs["fragments"]["remote_evaluated"] + inputs["fragments"]["cache_reused"]
    if inputs["inventory_complete"] and inputs["fragments"]["total"] == 0:
        return "no_eligible_content"
    if evaluated == 0:
        return "no_successful_evaluation"
    if inputs["fragments"]["above_threshold"] == 0:
        return "no_score_above_threshold"
    if omitted_stale > 0 and omitted_stale == inputs["fragments"]["above_threshold"]:
        return "no_fresh_excerpt"
    return "no_excerpt_fits"


def _assemble_result(inputs: dict, ranges: List[dict], represented: int) -> dict:
    excerpts = [_to_excerpt(range_) for range_ in ranges]
    fragments = inputs["fragments"]
    files = inputs["files"]
    usage = inputs["usage"]
    preflight = inputs["preflight"]
    evaluated = fragments["remote_evaluated"] + fragments["cache_reused"]
    omitted_stale = fragments["omitted_stale"]
    omitted_by_budget = max(0, fragments["above_threshold"] - represented - omitted_stale)
    fully_scanned = (not inputs.get("rejected") and inputs["inventory_complete"]
                     and fragments["total"] is not None and fragments["not_evaluated"] == 0
                     and files["unreadable"] == 0 and files["changed_before_return"] == 0
                     and all(reason in ("USAGE_UNKNOWN", "ESTIMATE_OVERRUN") for reason in inputs["stop_reasons"]))
    if inputs.get("rejected"):
        status = "rejected"
    elif fully_scanned:
        status = "complete"
    elif (evaluated == 0 and any(reason in ("PROVIDER_AUTH", "PROVIDER_QUOTA", "PROVIDER_UNAVAILABLE",
                                            "INVALID_PROVIDER_RESPONSE", "PROVIDER_RATE_LIMIT",
                                            "RESOURCE_EXHAUSTED", "CANCELLED")
                                 for reason in inputs["stop_reasons"])):
        status = "error"
    else:
        status = "partial"

    return {
        "schema_version": SCHEMA_VERSION,
        "search_id": inputs["search_id"],
        "status": status,
        "excerpts": excerpts,
        "report": {
            "scope": list(inputs["scope"]),
            "inventory_complete": inputs["inventory_complete"],
            "scope_fully_scanned": fully_scanned,
            "files": {
                "discovered": files["discovered"],
                "eligible": files["eligible"],
                "excluded_by_reason": dict(files["excluded_by_reason"]),
                "unreadable": files["unreadable"],
                "changed_before_return": files["changed_before_return"],
            },
            "fragments": {
                "total": fragments["total"],
                "remote_evaluated": fragments["remote_evaluated"],
                "cache_reused": fragments["cache_reused"],
                "not_evaluated": fragments["not_evaluated"],
                "below_threshold": fragments["below_threshold"],
                "above_threshold": fragments["above_threshold"],
                "represented_in_response": represented,
                "omitted_by_response_budget": omitted_by_budget,
                "omitted_stale": omitted_stale,
            },
            "selection": {
                "outcome": _selection_outcome(inputs, len(excerpts), omitted_stale),
                "threshold": inputs["threshold"],
                "ranges_returned": len(excerpts),
                "duplicate_ranges_collapsed": inputs["duplicate_ranges_collapsed"],
            },
            "usage": {
                "provider_request_attempts": usage["provider_request_attempts"],
                "provider_input_tokens_reported": (usage["input_tokens_known_subtotal"]
                                                   if usage["attempts_with_unknown_usage"] == 0 else None),
                "provider_input_tokens_known_subtotal": usage["input_tokens_known_subtotal"],
                "provider_input_tokens_estimated": usage["input_tokens_estimated"],
                "estimated_cost_usd": usage["estimated_cost_usd"],
                "reported_cost_usd": usage["reported_cost_usd"],
                "attempts_with_unknown_usage": usage["attempts_with_unknown_usage"],
                "transmitted_bytes": usage["transmitted_bytes"],
                "elapsed_ms": usage["elapsed_ms"],
            },
            "response_budget": {
                "requested_tokens": inputs["requested_tokens"],
                "counter": inputs["counter_id"],
                "accounting": "reference_tokenizer",
            },
            "preflight": {
                "planned_remote_fragments": preflight["planned_remote_fragments"],
                "planned_cache_hits": preflight["planned_cache_hits"],
                "estimated_first_attempt_tokens": preflight["estimated_first_attempt_tokens"],
                "estimated_first_attempt_cost_usd": preflight["estimated_first_attempt_cost_usd"],
                "estimated_first_attempt_requests": preflight["estimated_first_attempt_requests"],
                "enabled_caps": dict(preflight["enabled_caps"]),
                "estimated_required_caps": dict(preflight["estimated_required_caps"]),
            },
            "stop_reasons": list(inputs["stop_reasons"]),
            "diagnostics_truncated": inputs["diagnostics_truncated"],
        },
    }


def render_search_result(inputs: dict, ranges: List[dict],
                         represented_for: Callable[[List[dict]], int]) -> dict:
    """Render a validated response that fits its declared budget."""
    current = list(ranges)
    removed = 0
    while True:
        result = _assemble_result(inputs, current, represented_for(current))
        validate_search_result(result)
        text = serialize_outcome(result)
        measured = count_reference_tokens(text)
        if measured <= inputs["requested_tokens"] or not current:
            if measured > inputs["requested_tokens"]:
                raise ResponseBudgetError(measured, inputs["requested_tokens"])
            return {"result": result, "text": text, "measured_tokens": measured, "removed_for_budget": removed}
        # Remove the lowest-priority range: lowest score, latest path and line.
        victim = 0
        for index in range(1, len(current)):
            candidate, worst = current[index], current[victim]
            if (candidate["score"] < worst["score"]
                    or (candidate["score"] == worst["score"] and candidate["path"] > worst["path"])
                    or (candidate["score"] == worst["score"] and candidate["path"] == worst["path"]
                        and candidate["start_line"] > worst["start_line"])):
                victim = index
        current.pop(victim)
        removed += 1


def error_fits_bounds(serialized: str) -> bool:
    return utf8_bytes(serialized) <= CONTRACT_LIMITS["error_bytes"] \
        and count_reference_tokens(serialized) <= CONTRACT_LIMITS["error_tokens"]
