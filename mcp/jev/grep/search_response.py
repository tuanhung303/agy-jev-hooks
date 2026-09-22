"""CLI/MCP response mapping (port of upstream search-response.ts)."""
from .contracts import CONTRACT_LIMITS, validate_search_result
from .render import error_fits_bounds, serialize_outcome
from .tokens import REFERENCE_COUNTER_ID, count_reference_tokens

CLI_EXIT_CODES = {"complete": 0, "rejected": 2, "partial": 3, "error": 4, "interrupted": 130}


def rendered_outcome(input_outcome: dict, counter_id: str = REFERENCE_COUNTER_ID) -> dict:
    if "error" in input_outcome:
        text = serialize_outcome(input_outcome)
        if not error_fits_bounds(text):
            raise ValueError("compact error exceeds its byte budget")
        return {"outcome": input_outcome, "text": text}
    validate_search_result(input_outcome)
    if input_outcome["report"]["response_budget"]["counter"] != counter_id:
        raise ValueError("reference counter identity mismatch")
    text = serialize_outcome(input_outcome)
    budget = input_outcome["report"]["response_budget"]["requested_tokens"]
    tokens = count_reference_tokens(text)
    if tokens > budget:
        raise ValueError("serialized response exceeds its reference-token budget")
    return {"outcome": input_outcome, "text": text}


def to_cli_search_response(input_outcome: dict, counter_id: str = REFERENCE_COUNTER_ID) -> dict:
    rendered = rendered_outcome(input_outcome, counter_id)
    outcome = rendered["outcome"]
    cancelled = (outcome.get("error", {}).get("code") == "CANCELLED"
                 if "error" in outcome else "CANCELLED" in outcome["report"]["stop_reasons"])
    status = outcome["status"]
    exit_code = CLI_EXIT_CODES["interrupted"] if cancelled else CLI_EXIT_CODES[status]
    return {"stdout": rendered["text"], "exit_code": exit_code}


def to_mcp_search_response(input_outcome: dict, counter_id: str = REFERENCE_COUNTER_ID) -> dict:
    rendered = rendered_outcome(input_outcome, counter_id)
    outcome = rendered["outcome"]
    return {"content": [{"type": "text", "text": rendered["text"]}],
            "isError": outcome["status"] in ("rejected", "error")}
