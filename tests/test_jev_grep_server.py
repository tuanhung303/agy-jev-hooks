"""Focused regression tests for jev_grep MCP server malformed-request robustness."""
import io
import json
import threading

from mcp.jev.grep import grep_server


def _serve(messages, run=None):
    if run is None:
        def run(arguments, base, cancel_event=None, started=None):
            return f"match: {arguments.get('pattern')}", {"outcome": "ranked"}

    stdin = io.StringIO("".join(json.dumps(m) + "\n" if isinstance(m, (dict, list)) else str(m) + "\n"
                                for m in messages))
    out, events = io.StringIO(), []
    grep_server.serve(stdin, out, base="/base", run=run, log=events.append)
    replies = []
    for line in out.getvalue().splitlines():
        line = line.strip()
        if line:
            replies.append(json.loads(line))
    return replies, events


def test_malformed_params_with_id_returns_jsonrpc_32602_and_server_continues():
    for bad_params in ([1, 2], "not-a-dict", 123, True, None):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": bad_params},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": bad_params},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        replies, _ = _serve(messages)
        assert len(replies) == 3, f"failed for bad_params={bad_params!r}"
        r1, r2, r3 = replies[0], replies[1], replies[2]

        assert r1["id"] == 1
        assert r1["error"]["code"] == -32602
        assert "result" not in r1

        assert r2["id"] == 2
        assert r2["error"]["code"] == -32602
        assert "result" not in r2

        assert r3["id"] == 3
        assert r3.get("result") == {}


def test_malformed_notification_is_ignored_and_following_ping_succeeds():
    messages = [
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": [1, 2, 3]},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": "not-an-object"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": None},
        {"jsonrpc": "2.0", "method": "tools/call", "params": [1, 2]},
        {"jsonrpc": "2.0", "id": 10, "method": "ping"},
    ]
    replies, _ = _serve(messages)
    # Notifications must never receive replies
    assert len(replies) == 1
    assert replies[0]["id"] == 10
    assert replies[0].get("result") == {}


def test_tools_call_with_non_object_arguments_returns_tool_error_not_crash():
    for bad_args in ([1, 2], [], "string", 123, False):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "jev_grep", "arguments": bad_args}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
        replies, events = _serve(messages, run=grep_server.grep_tool.run)
        assert len(replies) == 2, f"failed for bad_args={bad_args!r}"
        by_id = {reply["id"]: reply for reply in replies}
        tool_reply, ping_reply = by_id[1], by_id[2]

        assert tool_reply["id"] == 1
        assert "error" not in tool_reply
        assert "result" in tool_reply
        assert tool_reply["result"]["isError"] is True
        assert tool_reply["result"]["content"][0]["text"] == "arguments must be an object"

        assert ping_reply["id"] == 2
        assert ping_reply.get("result") == {}


def test_valid_request_and_async_tool_execution_preserved():
    gate, running = threading.Barrier(2, timeout=5), []

    def concurrent_run(arguments, base, cancel_event=None, started=None):
        running.append(arguments["pattern"])
        gate.wait()
        return f"{base}:{arguments['pattern']}", {"outcome": "ranked"}

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "jev_grep", "arguments": {"pattern": "alpha"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "jev_grep", "arguments": {"pattern": "beta"}}},
    ]
    replies, events = _serve(messages, run=concurrent_run)
    by_id = {r["id"]: r for r in replies}
    assert by_id[1]["result"]["serverInfo"]["name"] == "jevgrep"
    assert by_id[2]["result"]["content"][0]["text"] == "/base:alpha"
    assert by_id[3]["result"]["content"][0]["text"] == "/base:beta"
    assert len(events) == 2


def test_live_mcp_stdio_session_malformed_followed_by_ping(capsys):
    # Tests that invalid json and malformed requests don't print any traceback to stderr
    messages = [
        "{invalid json",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["malformed"]},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "jev_grep", "arguments": ["bad"]}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ]
    replies, _ = _serve(messages)
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    by_id = {reply.get("id"): reply for reply in replies}
    assert len(replies) == 4 and by_id[None]["error"]["code"] == -32700
    assert by_id[1]["error"]["code"] == -32602
    assert by_id[2]["result"]["isError"] is True
    assert by_id[3]["result"] == {}
