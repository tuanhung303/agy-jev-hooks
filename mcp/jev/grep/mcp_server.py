"""Local stdio MCP server (port of upstream mcp.ts).

Thin adapter over the shared engine: validates protocol framing, exposes one
tool, maps outcomes onto MCP results. No search logic here, and it cannot widen
authorization: the tool schema has no root, credential, endpoint, model or cap
field. Startup scans nothing and contacts no provider. One active search, one
queued search, then BUSY. A cancelled call never produces a later result.
Closing stdin ends the process cleanly.
"""
import json
import sys
import threading
import uuid
from typing import Callable, Dict, IO, Optional

from .contracts import create_search_error
from .engine import SearchEngine

MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOOL_NAME = "semantic_search_code"

TOOL_DESCRIPTION = (
    "Search authorized repository code by behavior, responsibility, or concept when exact identifiers are "
    "unknown. Returns original excerpts and coverage information under a response budget. Source evaluation "
    "is remote when configured. Use normal exact search for a known symbol, path, or literal. Partial coverage "
    "and empty selections do not establish absence; inspect the report and continue investigation as needed."
)

TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Behaviour-oriented search question."},
        "scope": {
            "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32,
            "description": "Repository-relative files or directories. Defaults to the whole authorized root.",
        },
        "max_context_tokens": {
            "type": "integer", "minimum": 1_024,
            "description": "Response budget in reference tokens.",
        },
        "allow_partial_scan": {
            "type": "boolean",
            "description": "Allow a deterministic partial scan instead of refusing a scope that exceeds an enabled cap.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def tool_result(outcome: dict) -> dict:
    from .search_response import to_mcp_search_response
    return to_mcp_search_response(outcome)


class _ActiveCall:
    def __init__(self, call_id, started_at_ms: int, arguments: dict):
        self.id = call_id
        self.started_at_ms = started_at_ms
        self.arguments = arguments
        self.cancel_event = threading.Event()
        self.done = threading.Event()


def run_mcp_server(engine: SearchEngine, input_stream: IO[str], output: IO[str], error_output: IO[str],
                   server_version: str, on_close: Optional[Callable[[], None]] = None) -> None:
    lock = threading.Lock()
    active: Dict[str, _ActiveCall] = {}
    state = {"running": None, "queued": None}  # type: ignore
    in_flight = set()

    def log(line: str) -> None:
        error_output.write(line + "\n")
        error_output.flush()

    def send(message: dict) -> None:
        output.write(json.dumps(message, separators=(",", ":")) + "\n")
        output.flush()

    def send_error(call_id, code: int, message: str) -> None:
        send({"jsonrpc": "2.0", "id": call_id, "error": {"code": code, "message": message}})

    def call_key(call_id) -> str:
        return json.dumps(call_id)

    def emit(call: _ActiveCall, payload=None, error: Optional[str] = None) -> None:
        """Terminal response decision and write share the lock with cancellation
        acceptance, so a cancelled call can never emit a later result."""
        with lock:
            if call.cancel_event.is_set():
                return
            if payload is not None:
                send({"jsonrpc": "2.0", "id": call.id, "result": payload})
            else:
                send_error(call.id, INTERNAL_ERROR, error or "the search failed")

    def execute(call: _ActiveCall) -> None:
        try:
            result = engine.search(call.arguments, {
                "cancel_event": call.cancel_event, "started_at_ms": call.started_at_ms,
                "search_id": None,
            })
            emit(call, payload=tool_result(result["outcome"]))
        except Exception:
            log("jevgrep mcp tool failure")
            emit(call, error="the search failed before a report could be produced")
        finally:
            with lock:
                active.pop(call_key(call.id), None)
                call.done.set()
                following = state["queued"]
                state["queued"] = None
                state["running"] = following  # slot stays reserved across promotion
            if following is not None:
                threading.Thread(target=execute, args=(following,), daemon=True).start()

    def handle_tool_call(call_id, params) -> None:
        record = params or {}
        if record.get("name") != TOOL_NAME:
            send_error(call_id, INVALID_PARAMS, "unknown tool")
            return
        with lock:
            if call_key(call_id) in active:
                send_error(call_id, INVALID_REQUEST, "request id is already in use")
                return
            if state["running"] is not None and state["queued"] is not None:
                send({"jsonrpc": "2.0", "id": call_id,
                      "result": tool_result(create_search_error("BUSY", str(uuid.uuid4())))})
                return
            call = _ActiveCall(call_id, engine.clock.now_ms, record.get("arguments") or {})
            active[call_key(call_id)] = call
            start_now = state["running"] is None
            if start_now:
                state["running"] = call  # reserve before any thread runs
            else:
                state["queued"] = call
        if start_now:
            threading.Thread(target=execute, args=(call,), daemon=True).start()

    def handle(message: dict) -> None:
        call_id = message.get("id")
        method = message.get("method", "")
        if call_id is None and not method.startswith("notifications/"):
            return
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion")
            protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
            send({"jsonrpc": "2.0", "id": call_id, "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "jevgrep", "version": server_version},
                "instructions": "One tool: semantic_search_code. Evidence is returned as original excerpts "
                                "under a response budget.",
            }})
        elif method in ("notifications/initialized", "notifications/roots/list_changed"):
            return
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": call_id, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": call_id, "result": {"tools": [{
                "name": TOOL_NAME, "description": TOOL_DESCRIPTION, "inputSchema": TOOL_INPUT_SCHEMA,
                "annotations": {"readOnlyHint": True, "openWorldHint": True, "title": "Semantic code search"},
            }]}})
        elif method == "tools/call":
            if call_id is None:
                return
            handle_tool_call(call_id, message.get("params"))
        elif method == "notifications/cancelled":
            request_id = (message.get("params") or {}).get("requestId")
            if not isinstance(request_id, (str, int, float)):
                return
            with lock:
                call = active.get(call_key(request_id))
                if call is not None:
                    call.cancel_event.set()
                    if state["queued"] is call:
                        state["queued"] = None
                        active.pop(call_key(call.id), None)
                        call.done.set()
        elif call_id is not None:
            send_error(call_id, METHOD_NOT_FOUND, "unsupported method")

    def consume(line: str) -> None:
        trimmed = line.strip()
        if not trimmed:
            return
        try:
            parsed = json.loads(trimmed)
        except ValueError:
            send_error(None, PARSE_ERROR, "invalid JSON message")
            log("jevgrep mcp received an unparsable message")
            return
        if not isinstance(parsed, dict):
            send_error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
            return
        call_id = parsed.get("id")
        valid_id = isinstance(call_id, (str, int, float)) and not isinstance(call_id, bool)
        if parsed.get("jsonrpc") != "2.0" or not isinstance(parsed.get("method"), str) \
                or ("id" in parsed and not valid_id):
            send_error(call_id if valid_id else None, INVALID_REQUEST, "invalid JSON-RPC request")
            return
        params = parsed.get("params")
        if params is not None and not isinstance(params, dict):
            if valid_id:
                send_error(call_id, INVALID_PARAMS, "expected object parameters")
            return
        handle(parsed)

    for line in input_stream:
        consume(line)
    # EOF: let pending calls settle so the process never exits mid-response.
    for call in list(active.values()):
        call.done.wait(timeout=360)
    if on_close is not None:
        on_close()
