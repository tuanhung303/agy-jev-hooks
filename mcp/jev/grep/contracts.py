"""Search request/result contract (port of upstream contracts.ts).

The validator is the integrity guarantee: counters partition, status asserts
coverage, early failures are rejections, every returned range is in scope and
above threshold. Shapes match schema_version "1".
"""
import math
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "1"
CONFIG_SCHEMA_VERSION = 1

CONTRACT_LIMITS = {
    "query_bytes": 8_192,
    "scope_entries": 32,
    "scope_bytes": 4_096,
    "min_response_tokens": 1_024,
    "default_response_tokens": 4_000,
    "max_response_tokens": 16_000,
    "error_bytes": 4_096,
    "error_tokens": 1_024,
    "diagnostic_events": 64,
}

SCAN_CAP_KEYS = (
    "estimated_cost_usd", "estimated_input_tokens", "transmitted_bytes",
    "request_attempts", "prepared_source_bytes", "candidate_files", "fragments",
)

EXCLUSION_REASONS = (
    "administrative", "credential_file", "operator_denied", "gitignored",
    "jevgrepignored", "dependency", "build_output", "generated", "minified",
    "unsupported_encoding", "binary", "file_too_large", "credential_pattern",
    "empty", "whitespace_only", "unsupported_long_line",
    "link", "outside_root", "not_regular_file",
)

STOP_REASONS = (
    "INVALID_REQUEST", "INVALID_CONFIG", "UNAUTHORIZED_SCOPE", "REMOTE_DISABLED",
    "CREDENTIAL_MISSING", "BUSY", "SCOPE_EXCEEDS_SCAN_BUDGET",
    "RESPONSE_BUDGET_TOO_SMALL", "INVENTORY_INCOMPLETE", "PREPARATION_LIMIT",
    "PROVIDER_AUTH", "PROVIDER_QUOTA", "PROVIDER_RATE_LIMIT", "PROVIDER_UNAVAILABLE",
    "INVALID_PROVIDER_RESPONSE", "USAGE_UNKNOWN", "SCAN_CAP_REACHED",
    "ESTIMATE_OVERRUN", "DEADLINE", "CANCELLED", "SOURCE_CHANGED", "RESOURCE_EXHAUSTED",
)

# code -> (status, retryable, fixed guidance). Owned here, never by a provider.
ERROR_DEFINITIONS: Dict[str, Tuple[str, bool, str]] = {
    "INVALID_REQUEST": ("rejected", False, "Check the query, relative scope paths and response budget."),
    "INVALID_CONFIG": ("rejected", False, "Correct the trusted configuration before searching."),
    "UNAUTHORIZED_SCOPE": ("rejected", False, "Choose an authorized relative scope without linked paths."),
    "REMOTE_DISABLED": ("rejected", False, "Enable remote evaluation in the trusted operator configuration."),
    "CREDENTIAL_MISSING": ("rejected", False, "Set the configured credential environment variable."),
    "BUSY": ("rejected", True, "Wait for the active search and pending search to finish."),
    "SCOPE_EXCEEDS_SCAN_BUDGET": ("rejected", False, "Narrow the scope or explicitly allow a partial scan."),
    "RESPONSE_BUDGET_TOO_SMALL": ("rejected", False, "Increase the response budget or narrow the scope."),
    "PROVIDER_AUTH": ("error", False, "Check the provider credential and access to the configured model."),
    "PROVIDER_QUOTA": ("error", False, "Check the provider account quota before searching again."),
    "PROVIDER_RATE_LIMIT": ("error", True, "Wait before retrying the search within the configured limits."),
    "PROVIDER_UNAVAILABLE": ("error", True, "Check provider availability; a dispatched attempt may have incurred usage."),
    "INVALID_PROVIDER_RESPONSE": ("error", False, "Check compatibility with the configured provider model and adapter."),
    "RESOURCE_EXHAUSTED": ("error", False, "Free local resources or narrow the search scope."),
    "CANCELLED": ("error", False, "The search was interrupted; dispatched attempts may have incurred usage."),
}

_ERROR_STATUS_STRINGS = ("rejected", "error")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")
_TRAILING_DOT_SPACE = re.compile(r"[. ]$")
_DEVICE_NAMES = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(\.|$)", re.IGNORECASE)
_SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]*$")


class ContractValidationError(Exception):
    """A response or request broke the published contract."""

    def __init__(self, path: str, rule: str):
        super().__init__(f"{path}: {rule}")
        self.path = path
        self.rule = rule


def require_contract(condition: bool, path: str, rule: str) -> None:
    if not condition:
        raise ContractValidationError(path, rule)


def _utf8_len(value: str, path: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ContractValidationError(path, "expected well-formed Unicode") from None


def normalized_path(input_path: str, path: str) -> str:
    require_contract(isinstance(input_path, str) and input_path != "", path, "expected a path string")
    require_contract(not input_path.startswith(("/", "\\")), path, "absolute and namespace paths are forbidden")
    require_contract(_CONTROL_CHARS.search(input_path) is None, path, "unsupported path syntax")
    segments = input_path.replace("\\", "/").split("/")
    require_contract(".." not in segments, path, "parent traversal is forbidden")
    parts = [part for part in segments if part not in ("", ".")]
    for part in parts:
        require_contract(_TRAILING_DOT_SPACE.search(part) is None, path, "ambiguous trailing path characters")
        require_contract(_DEVICE_NAMES.match(part) is None, path, "device names are forbidden")
    return "/".join(parts) or "."


def normalize_scope(scope: List[str]) -> List[str]:
    """Deduplicate, drop entries covered by another entry, sort."""
    paths = []
    for value in scope:
        normalized = normalized_path(value, "$.scope")
        if normalized not in paths:
            paths.append(normalized)
    kept = [value for value in paths
            if not any(parent != value and (parent == "." or value.startswith(parent + "/")) for parent in paths)]
    return sorted(kept)


class ResponseLimits:
    def __init__(self, default_response_tokens: Optional[int] = None, max_response_tokens: Optional[int] = None):
        self.default_response_tokens = default_response_tokens or CONTRACT_LIMITS["default_response_tokens"]
        self.max_response_tokens = max_response_tokens or CONTRACT_LIMITS["max_response_tokens"]


def parse_search_request(raw: Any, limits: Optional[ResponseLimits] = None) -> Dict[str, Any]:
    """Lexical validation only; filesystem authorization stays with the source reader."""
    limits = limits or ResponseLimits()
    require_contract(isinstance(raw, dict), "$", "expected an object request")
    query = raw.get("query")
    require_contract(isinstance(query, str) and 0 < _utf8_len(query, "$.query") <= CONTRACT_LIMITS["query_bytes"],
                     "$.query", "expected a bounded non-empty question")

    scope_raw = raw.get("scope", ["."])
    require_contract(isinstance(scope_raw, list) and 1 <= len(scope_raw) <= CONTRACT_LIMITS["scope_entries"],
                     "$.scope", "expected 1..%d scope entries" % CONTRACT_LIMITS["scope_entries"])
    for item in scope_raw:
        require_contract(isinstance(item, str)
                         and _utf8_len(item, "$.scope") <= CONTRACT_LIMITS["scope_bytes"],
                         "$.scope", "scope entries are bounded strings")
    require_contract(sum(_utf8_len(item, "$.scope") for item in scope_raw) <= CONTRACT_LIMITS["scope_bytes"],
                     "$.scope", "combined scope UTF-8 byte limit exceeded")
    # upstream semantics: the caller's spelling is normalized, never rejected
    scope = normalize_scope([item for item in scope_raw if isinstance(item, str)])

    max_context_tokens = raw.get("max_context_tokens", limits.default_response_tokens)
    require_contract(isinstance(max_context_tokens, int) and not isinstance(max_context_tokens, bool)
                     and CONTRACT_LIMITS["min_response_tokens"] <= max_context_tokens <= limits.max_response_tokens,
                     "$.max_context_tokens", "response budget outside supported bounds")

    allow_partial_scan = raw.get("allow_partial_scan", False)
    require_contract(isinstance(allow_partial_scan, bool), "$.allow_partial_scan", "expected a boolean")
    return {"query": query, "scope": scope, "max_context_tokens": max_context_tokens,
            "allow_partial_scan": allow_partial_scan}


def create_search_error(code: str, search_id: str) -> Dict[str, Any]:
    status, retryable, message = ERROR_DEFINITIONS[code]
    return {"schema_version": SCHEMA_VERSION, "search_id": search_id, "status": status,
            "error": {"code": code, "message": message, "retryable": retryable}}


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite_number(value: Any, minimum: float = 0.0) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= minimum)


def _validate_structure(value: dict) -> None:
    """Phase 1: strict field shapes before any relational invariant runs."""
    require_contract(isinstance(value, dict), "$", "expected an object result")
    require_contract(value.get("schema_version") == SCHEMA_VERSION, "$.schema_version",
                     "unexpected schema version")
    search_id = value.get("search_id")
    require_contract(isinstance(search_id, str) and len(search_id) <= 128
                     and _IDENTIFIER.match(search_id) is not None,
                     "$.search_id", "expected a bounded identifier")
    require_contract(value.get("status") in ("complete", "partial", "rejected", "error"),
                     "$.status", "unexpected status")

    excerpts = value.get("excerpts")
    require_contract(isinstance(excerpts, list), "$.excerpts", "expected an excerpt array")
    for excerpt in excerpts:
        require_contract(isinstance(excerpt, dict), "$.excerpts", "expected excerpt objects")
        require_contract(isinstance(excerpt.get("path"), str), "$.excerpts[].path", "expected a path string")
        require_contract(_is_count(excerpt.get("start_line")) and excerpt.get("start_line", 0) >= 1
                         and _is_count(excerpt.get("end_line")) and excerpt.get("end_line", 0) >= 1,
                         "$.excerpts[].start_line", "line numbers must be positive integers")
        require_contract(excerpt["start_line"] <= excerpt["end_line"], "$.excerpts",
                         "inverted inclusive line range")
        digest = excerpt.get("file_sha256")
        require_contract(isinstance(digest, str) and _SHA256_HEX.match(digest) is not None,
                         "$.excerpts[].file_sha256", "expected a lowercase SHA-256 hex digest")
        require_contract(_finite_number(excerpt.get("score")) and excerpt["score"] <= 1,
                         "$.excerpts[].score", "expected a probability")
        require_contract(isinstance(excerpt.get("code"), str) and excerpt["code"] != "",
                         "$.excerpts[].code", "an excerpt must contain source text")

    report = value.get("report")
    require_contract(isinstance(report, dict), "$.report", "expected a report object")
    for key in ("scope", "inventory_complete", "scope_fully_scanned", "files", "fragments",
                "selection", "usage", "response_budget", "preflight", "stop_reasons",
                "diagnostics_truncated"):
        require_contract(key in report, "$.report", f"missing field {key}")
    require_contract(isinstance(report["inventory_complete"], bool)
                     and isinstance(report["scope_fully_scanned"], bool)
                     and isinstance(report["diagnostics_truncated"], bool),
                     "$.report", "flags must be strict booleans")
    require_contract(isinstance(report["scope"], list)
                     and all(isinstance(item, str) for item in report["scope"]),
                     "$.report.scope", "expected scope strings")
    require_contract(isinstance(report["stop_reasons"], list)
                     and all(item in STOP_REASONS for item in report["stop_reasons"]),
                     "$.report.stop_reasons", "expected known stop reasons")

    budget = report["response_budget"]
    require_contract(isinstance(budget, dict), "$.report.response_budget", "expected a response budget")
    require_contract(_is_count(budget.get("requested_tokens"))
                     and budget["requested_tokens"] >= CONTRACT_LIMITS["min_response_tokens"],
                     "$.report.response_budget", "requested_tokens outside supported bounds")
    require_contract(isinstance(budget.get("counter"), str) and budget["counter"] != "",
                     "$.report.response_budget", "expected a counter identifier")
    require_contract(budget.get("accounting") == "reference_tokenizer",
                     "$.report.response_budget", "unexpected accounting basis")

    files = report["files"]
    fragments = report["fragments"]
    selection = report["selection"]
    usage = report["usage"]
    preflight = report["preflight"]
    for obj, keys in (
        (files, ("discovered", "eligible", "excluded_by_reason", "unreadable", "changed_before_return")),
        (fragments, ("total", "remote_evaluated", "cache_reused", "not_evaluated", "below_threshold",
                     "above_threshold", "represented_in_response", "omitted_by_response_budget",
                     "omitted_stale")),
        (selection, ("outcome", "threshold", "ranges_returned", "duplicate_ranges_collapsed")),
        (usage, ("provider_request_attempts", "provider_input_tokens_reported",
                 "provider_input_tokens_known_subtotal", "provider_input_tokens_estimated",
                 "estimated_cost_usd", "reported_cost_usd", "attempts_with_unknown_usage",
                 "transmitted_bytes", "elapsed_ms")),
        (preflight, ("planned_remote_fragments", "planned_cache_hits", "estimated_first_attempt_tokens",
                     "estimated_first_attempt_cost_usd", "estimated_first_attempt_requests",
                     "enabled_caps", "estimated_required_caps")),
    ):
        require_contract(isinstance(obj, dict), "$.report", "expected report objects")
        for key in keys:
            require_contract(key in obj, "$.report", f"missing field {key}")
    require_contract(isinstance(files["excluded_by_reason"], dict), "$.report.files",
                     "expected exclusion counts")
    require_contract(_finite_number(selection["threshold"]) and selection["threshold"] <= 1,
                     "$.report.selection", "threshold must be a probability")
    require_contract(fragments["total"] is None or _is_count(fragments["total"]),
                     "$.report.fragments", "fragment total must be null or a count")
    require_contract(usage["provider_input_tokens_reported"] is None
                     or _is_count(usage["provider_input_tokens_reported"]),
                     "$.report.usage", "reported tokens must be a count or null")
    for key in ("estimated_cost_usd", "reported_cost_usd"):
        require_contract(usage[key] is None or _finite_number(usage[key]),
                         "$.report.usage", "USD amounts must be finite or null")
    # USD caps are decimals; every other cap is an integer count
    for caps in (preflight["enabled_caps"], preflight["estimated_required_caps"]):
        require_contract(isinstance(caps, dict), "$.report.preflight", "expected cap maps")
        for key, value in caps.items():
            require_contract(key in SCAN_CAP_KEYS, "$.report.preflight", "unknown scan cap key")
            require_contract(value is None or _finite_number(value),
                             "$.report.preflight", "cap values must be finite or null")
            if key != "estimated_cost_usd":
                require_contract(value is None or _is_count(value),
                                 "$.report.preflight", "non-USD caps must be counts or null")


def _is_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _is_usd(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def validate_search_result(value: Dict[str, Any]) -> None:
    """Port of upstream validateResult: structure first, then identity checks."""
    _validate_structure(value)
    status = value.get("status")
    excerpts = value.get("excerpts")
    report = value.get("report")

    files = report["files"]
    f = report["fragments"]
    selection = report["selection"]
    usage = report["usage"]
    preflight = report["preflight"]
    reasons = report["stop_reasons"]

    require_contract(isinstance(reasons, list) and len(set(reasons)) == len(reasons),
                     "$.report", "stop reasons must be unique")
    for reason in reasons:
        require_contract(reason in STOP_REASONS, "$.report.stop_reasons", "unknown stop reason")
    require_contract(selection["outcome"] in (
        "selected", "no_eligible_content", "no_score_above_threshold",
        "no_excerpt_fits", "no_successful_evaluation", "no_fresh_excerpt", "preflight_rejected",
    ), "$.report.selection.outcome", "unexpected selection outcome")

    def check(condition: bool, rule: str) -> None:
        require_contract(condition, "$.report", rule)

    evaluated = f["remote_evaluated"] + f["cache_reused"]
    known_total = evaluated + f["not_evaluated"]
    check(all(_is_count(item) for item in (
        files["discovered"], files["eligible"], files["unreadable"], files["changed_before_return"],
        f["remote_evaluated"], f["cache_reused"], f["not_evaluated"], f["below_threshold"],
        f["above_threshold"], f["represented_in_response"], f["omitted_by_response_budget"],
        f["omitted_stale"], selection["ranges_returned"], selection["duplicate_ranges_collapsed"],
        usage["provider_request_attempts"], usage["provider_input_tokens_known_subtotal"],
        usage["provider_input_tokens_estimated"], usage["attempts_with_unknown_usage"],
        usage["transmitted_bytes"], usage["elapsed_ms"], preflight["planned_cache_hits"],
    )), "counters must be non-negative integers")

    check(evaluated == f["below_threshold"] + f["above_threshold"],
          "successful evaluations must equal threshold categories")
    check(f["above_threshold"] == f["represented_in_response"] + f["omitted_by_response_budget"] + f["omitted_stale"],
          "qualifying fragments must equal represented, budget-omitted and stale categories")
    check(f["total"] is None or f["total"] == known_total, "fragment total does not match terminal categories")
    check(report["inventory_complete"] or f["total"] is None, "incomplete inventory requires an unknown fragment total")
    check(files["unreadable"] == 0 or f["total"] is None, "unreadable source prevents a known prepared total")
    excluded_total = sum(files["excluded_by_reason"].values())
    check(all(_is_count(v) for v in files["excluded_by_reason"].values()), "exclusion counts must be counts")
    check(files["eligible"] + excluded_total <= files["discovered"],
          "eligible and excluded file counts exceed discovered files")
    check(files["unreadable"] <= files["discovered"] and files["changed_before_return"] <= files["eligible"],
          "file subset count exceeds its parent")
    check(known_total == 0 or files["eligible"] > 0, "known fragments require an eligible file")
    check(f["omitted_stale"] == 0 or files["changed_before_return"] > 0, "stale fragments require changed files")
    check(files["changed_before_return"] == 0 or "SOURCE_CHANGED" in reasons, "changed files require SOURCE_CHANGED")
    check(selection["ranges_returned"] == len(excerpts), "range count must equal excerpt count")
    check(selection["ranges_returned"] <= f["represented_in_response"], "ranges exceed represented fragments")
    check((f["represented_in_response"] == 0) == (len(excerpts) == 0), "represented fragments require returned ranges")
    check(selection["duplicate_ranges_collapsed"] <= f["above_threshold"], "collapsed duplicates exceed qualifying fragments")

    can_be_complete = (report["inventory_complete"] and f["total"] is not None and f["not_evaluated"] == 0
                       and files["unreadable"] == 0 and files["changed_before_return"] == 0)
    check(not report["scope_fully_scanned"] or can_be_complete,
          "full coverage contradicts inventory, evaluations or freshness")
    check((status == "complete") == report["scope_fully_scanned"], "only complete status asserts full coverage")
    if status == "complete":
        check(all(reason in ("USAGE_UNKNOWN", "ESTIMATE_OVERRUN") for reason in reasons),
              "complete status contains a stopping failure")
    else:
        check(len(reasons) > 0, "an incomplete result requires a reason")
    if status == "rejected":
        check(evaluated == 0 and usage["provider_request_attempts"] == 0, "preflight rejection cannot execute evaluations")
        check("SCOPE_EXCEEDS_SCAN_BUDGET" in reasons, "reported rejection requires the scan-budget reason")
        check(len(preflight["enabled_caps"]) > 0, "scan-budget rejection requires an enabled cap")
    if status == "error":
        check(evaluated == 0, "fatal failure after a successful evaluation must be partial")
        check(any(reason in ERROR_DEFINITIONS and ERROR_DEFINITIONS[reason][0] == "error" for reason in reasons),
              "error status requires a fatal reason")
    if status == "partial" and evaluated == 0:
        always_fatal = [r for r in ("PROVIDER_AUTH", "PROVIDER_QUOTA", "RESOURCE_EXHAUSTED") if r in reasons]
        execution_stopped = [r for r in ("DEADLINE", "CANCELLED", "SCAN_CAP_REACHED", "PREPARATION_LIMIT") if r in reasons]
        failed_provider = [r for r in ("PROVIDER_RATE_LIMIT", "PROVIDER_UNAVAILABLE", "INVALID_PROVIDER_RESPONSE")
                           if r in reasons]
        check(not always_fatal and (not failed_provider or bool(execution_stopped)),
              "fatal failure without a successful evaluation requires error status")

    early_rejection = [r for r in ("INVALID_REQUEST", "INVALID_CONFIG", "UNAUTHORIZED_SCOPE",
                                   "REMOTE_DISABLED", "CREDENTIAL_MISSING", "BUSY",
                                   "RESPONSE_BUDGET_TOO_SMALL") if r in reasons]
    check(not early_rejection or status == "rejected", "early request or configuration failures must be rejected")

    if status == "rejected":
        expected_selection = "preflight_rejected"
    elif len(excerpts) > 0:
        expected_selection = "selected"
    elif report["inventory_complete"] and f["total"] == 0:
        expected_selection = "no_eligible_content"
    elif evaluated == 0:
        expected_selection = "no_successful_evaluation"
    elif f["above_threshold"] == 0:
        expected_selection = "no_score_above_threshold"
    elif f["omitted_stale"] == f["above_threshold"]:
        expected_selection = "no_fresh_excerpt"
    else:
        expected_selection = "no_excerpt_fits"
    check(selection["outcome"] == expected_selection, "selection outcome violates empty-selection precedence")

    check(_is_probability(selection["threshold"]), "threshold must be a probability")
    seen_ranges = set()
    hashes: Dict[str, str] = {}
    scope = report["scope"]
    check(scope == normalize_scope(list(scope)), "expected canonical, deduplicated scope")
    for excerpt in excerpts:
        require_contract(isinstance(excerpt, dict), "$.excerpts", "expected excerpt objects")
        path = excerpt.get("path")
        check(isinstance(path, str) and path != "." and normalized_path(path, "$.excerpts[].path") == path,
              "expected a canonical relative file path")
        check(_is_probability(excerpt.get("score")) and excerpt["score"] >= selection["threshold"],
              "returned score is below the selection threshold")
        check(any(entry == "." or path == entry or path.startswith(entry + "/") for entry in scope),
              "excerpt is outside the requested scope")
        check(isinstance(excerpt.get("code"), str) and excerpt["code"] != "", "an excerpt must contain source text")
        check(isinstance(excerpt.get("start_line"), int) and excerpt["start_line"] >= 1
              and excerpt["end_line"] >= excerpt["start_line"], "inverted inclusive line range")
        digest = excerpt.get("file_sha256")
        check(isinstance(digest, str) and _SHA256_HEX.match(digest) is not None,
              "expected a lowercase SHA-256 hex digest")
        check(path not in hashes or hashes[path] == digest, "one file cannot have multiple returned snapshots")
        hashes[path] = digest
        key = (path, excerpt["start_line"], excerpt["end_line"])
        check(key not in seen_ranges, "duplicate returned source range")
        seen_ranges.add(key)
    check(len(hashes) <= files["eligible"], "returned files exceed eligible files")

    check(usage["attempts_with_unknown_usage"] <= usage["provider_request_attempts"],
          "unknown attempts exceed dispatched attempts")
    reported = usage["provider_input_tokens_reported"]
    expected_reported = usage["provider_input_tokens_known_subtotal"] if usage["attempts_with_unknown_usage"] == 0 else None
    check(reported == expected_reported, "all-attempt usage must be null if any attempt has unknown usage")
    check(_is_count(reported) if reported is not None else True, "reported token count must be a count")
    check(usage["provider_input_tokens_estimated"] >= usage["provider_input_tokens_known_subtotal"],
          "estimate cannot erase known usage")
    check(usage["provider_input_tokens_estimated"]
          >= usage["provider_input_tokens_known_subtotal"] + usage["attempts_with_unknown_usage"],
          "each unknown dispatched attempt must retain a positive token reservation")
    check(usage["attempts_with_unknown_usage"] > 0
          or usage["provider_input_tokens_estimated"] == usage["provider_input_tokens_known_subtotal"],
          "fully known usage must replace token reservations")
    check(usage["attempts_with_unknown_usage"] < usage["provider_request_attempts"]
          or usage["provider_input_tokens_known_subtotal"] == 0,
          "no known attempts can contribute a known token subtotal")
    check(usage["attempts_with_unknown_usage"] == 0 or "USAGE_UNKNOWN" in reasons, "unknown usage requires USAGE_UNKNOWN")
    check(f["remote_evaluated"] == 0 or usage["provider_request_attempts"] > 0,
          "remote evaluations require a provider attempt")
    check(_is_usd(usage["estimated_cost_usd"]) and _is_usd(usage["reported_cost_usd"]), "USD amounts must be counts or null")
    if usage["provider_request_attempts"] == 0:
        check(usage["provider_input_tokens_estimated"] == 0 and usage["transmitted_bytes"] == 0
              and usage["estimated_cost_usd"] in (None, 0) and usage["reported_cost_usd"] in (None, 0),
              "zero-call searches cannot report incurred usage")

    enabled_caps = preflight["enabled_caps"]
    required_caps = preflight["estimated_required_caps"]
    check(sorted(enabled_caps) == sorted(required_caps), "cap maps must have exactly the same keys")
    if f["total"] is not None:
        check(preflight["planned_remote_fragments"] is not None
              and preflight["planned_remote_fragments"] + preflight["planned_cache_hits"] == f["total"],
              "preflight remote fragments and potential cache hits must partition the prepared total")
        check(preflight["estimated_first_attempt_tokens"] is not None
              and preflight["estimated_first_attempt_requests"] is not None,
              "finished preparation requires first-attempt estimates")
        check(all(v is not None for v in required_caps.values()), "known preparation cannot have unknown cap requirements")
    else:
        check(preflight["planned_remote_fragments"] is None,
              "incomplete preparation cannot claim an exact full remote plan")

    comparable = {
        "fragments": f["total"],
        "request_attempts": preflight["estimated_first_attempt_requests"],
        "estimated_input_tokens": preflight["estimated_first_attempt_tokens"],
        "estimated_cost_usd": preflight["estimated_first_attempt_cost_usd"],
    }
    for key, expected in comparable.items():
        check(key not in enabled_caps or required_caps.get(key) == expected,
              "required cap quantity disagrees with its preflight estimate")
    if status == "rejected":
        def exceeds_cap() -> bool:
            for key in enabled_caps:
                required = required_caps.get(key)
                enabled = enabled_caps[key]
                if required is None:
                    return "PREPARATION_LIMIT" in reasons
                if required > enabled:
                    return True
            return False
        check(exceeds_cap(), "preflight rejection must identify an exceeded cap or incomplete limited preparation")
