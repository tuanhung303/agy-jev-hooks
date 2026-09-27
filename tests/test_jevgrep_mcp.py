"""MCP stdio server smoke test through a real subprocess: framing, one tool,
offline rejection outcome, clean shutdown on stdin EOF."""
import json
import os
import subprocess
import sys

import pytest

from mcp.jev.grep.config import default_configuration, dump_configuration_yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def profile(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    config = default_configuration(str(repo))  # remote evaluation disabled by default
    path = tmp_path / "profile.yaml"
    path.write_text(dump_configuration_yaml(config), encoding="utf-8")
    return path


def _exchange(lines, profile_path):
    env = dict(os.environ, PYTHONPATH=PROJECT_ROOT, JEVGREP_CACHE_HOME=str(profile_path.parent / "cache"))
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp.jev.grep", "mcp", "--config", str(profile_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=PROJECT_ROOT)
    stdout, stderr = process.communicate("\n".join(lines) + "\n", timeout=120)
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return process.returncode, responses, stderr


def test_mcp_protocol_smoke(profile):
    calls = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "semantic_search_code",
                               "arguments": {"query": "where is the entry point?"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "unknown/method"}),
    ]
    code, responses, _ = _exchange(calls, profile)
    assert code == 0
    by_id = {response.get("id"): response for response in responses if "id" in response}

    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert by_id[1]["result"]["serverInfo"]["name"] == "jevgrep"

    tools = by_id[2]["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["semantic_search_code"]
    assert "query" in tools[0]["inputSchema"]["properties"]

    # remote evaluation disabled: a live call is a bounded rejection, never a dispatch
    result = by_id[3]["result"]
    assert result["isError"] is True
    outcome = json.loads(result["content"][0]["text"])
    assert outcome["status"] == "rejected"
    assert outcome["error"]["code"] == "REMOTE_DISABLED"

    assert by_id[4]["error"]["code"] == -32601


def test_mcp_rejects_unparsable_frames(profile):
    code, responses, stderr = _exchange(["this is not json"], profile)
    assert code == 0
    errors = [response for response in responses if "error" in response]
    assert errors and errors[0]["error"]["code"] == -32700


def test_mcp_startup_from_root_dir_with_single_profile(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    config = default_configuration(str(repo))
    config_home = tmp_path / "conf"
    config_home.mkdir()
    profile = config_home / "repo.yaml"
    profile.write_text(dump_configuration_yaml(config), encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=PROJECT_ROOT,
               JEVGREP_CONFIG_HOME=str(config_home),
               JEVGREP_CACHE_HOME=str(tmp_path / "cache"))
    env.pop("JEVGREP_PROFILE", None)
    # Run from root "/" without --config
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp.jev.grep", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd="/")
    calls = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "semantic_search_code",
                               "arguments": {"query": "where is run?"}}}),
    ]
    stdout, stderr = process.communicate("\n".join(calls) + "\n", timeout=120)
    assert process.returncode == 0
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    by_id = {resp.get("id"): resp for resp in responses if "id" in resp}
    assert by_id[1]["result"]["serverInfo"]["name"] == "jevgrep"
    assert by_id[2]["result"]["tools"][0]["name"] == "semantic_search_code"
    assert by_id[3]["result"]["isError"] is True
    outcome = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert outcome["error"]["code"] == "REMOTE_DISABLED"


def test_mcp_startup_from_root_dir_resolves_via_root_uri(tmp_path):
    repo = tmp_path / "repo_a"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    config = default_configuration(str(repo))
    config_home = tmp_path / "conf"
    config_home.mkdir()
    # Create two profiles so single-profile fallback does not apply
    profile_a = config_home / "repo_a.yaml"
    profile_a.write_text(dump_configuration_yaml(config), encoding="utf-8")
    profile_b = config_home / "repo_b.yaml"
    profile_b.write_text(dump_configuration_yaml(default_configuration(str(tmp_path / "repo_b"))), encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=PROJECT_ROOT,
               JEVGREP_CONFIG_HOME=str(config_home),
               JEVGREP_CACHE_HOME=str(tmp_path / "cache"))
    env.pop("JEVGREP_PROFILE", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp.jev.grep", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd="/")
    calls = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18",
                               "rootUri": f"file://{repo}"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "semantic_search_code",
                               "arguments": {"query": "where is run?"}}}),
    ]
    stdout, stderr = process.communicate("\n".join(calls) + "\n", timeout=120)
    assert process.returncode == 0
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    by_id = {resp.get("id"): resp for resp in responses if "id" in resp}
    assert by_id[1]["result"]["serverInfo"]["name"] == "jevgrep"
    assert by_id[2]["result"]["isError"] is True
    outcome = json.loads(by_id[2]["result"]["content"][0]["text"])
    assert outcome["error"]["code"] == "REMOTE_DISABLED"


def test_mcp_unresolvable_profile_returns_error_outcome_not_crash(tmp_path):
    empty_conf = tmp_path / "empty_conf"
    empty_conf.mkdir()
    env = dict(os.environ, PYTHONPATH=PROJECT_ROOT,
               JEVGREP_CONFIG_HOME=str(empty_conf),
               JEVGREP_CACHE_HOME=str(tmp_path / "cache"))
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp.jev.grep", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd="/")
    calls = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "semantic_search_code",
                               "arguments": {"query": "where is run?"}}}),
    ]
    stdout, stderr = process.communicate("\n".join(calls) + "\n", timeout=120)
    assert process.returncode == 0
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    by_id = {resp.get("id"): resp for resp in responses if "id" in resp}
    assert by_id[1]["result"]["serverInfo"]["name"] == "jevgrep"
    assert by_id[2]["result"]["isError"] is True
    outcome = json.loads(by_id[2]["result"]["content"][0]["text"])
    assert outcome["error"]["code"] == "INVALID_CONFIG"


# This proves profile selection only: credential resolution short-circuits before
# inventory. resolve_scoped_profile unit tests prove that scope is correctly rebased.
def test_mcp_call_scope_prefers_nested_profile_over_startup_anchor(tmp_path):
    """Regression: a nested profile carved out of a broader deny rule (for
    example tmp/worktrees/ under a repo that denies tmp/**) must win for a
    call whose scope names it, even though the process started up already
    bound to the outer profile (cwd matched it at spawn, before any call
    arrived). The engine used to be resolved once and cached for the whole
    process life; a later, differently-scoped call kept reusing the first
    profile regardless of what its own scope named."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    private_dir = repo / "private"
    private_dir.mkdir()
    (private_dir / "secret_app.py").write_text("def run():\n    return 2\n", encoding="utf-8")

    config_home = tmp_path / "conf"
    config_home.mkdir()
    outer_config = default_configuration(str(repo))  # remote disabled -> REMOTE_DISABLED
    (config_home / "outer.yaml").write_text(dump_configuration_yaml(outer_config), encoding="utf-8")
    inner_config = default_configuration(str(private_dir))
    inner_config["remote_evaluation_enabled"] = True  # unresolvable credential -> CREDENTIAL_MISSING
    (config_home / "inner.yaml").write_text(dump_configuration_yaml(inner_config), encoding="utf-8")

    fake_home = tmp_path / "fake_home"  # no secrets.env / sage.env fallback to find a real key in
    fake_home.mkdir()
    env = dict(os.environ, PYTHONPATH=PROJECT_ROOT,
               JEVGREP_CONFIG_HOME=str(config_home),
               JEVGREP_CACHE_HOME=str(tmp_path / "cache"),
               HOME=str(fake_home))
    env.pop("JEVGREP_PROFILE", None)
    env.pop("TYPESAFE_API_KEY", None)

    # No --config: the process resolves its own profile from cwd at startup,
    # same as a real MCP registration. cwd matches "outer" here, on purpose.
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp.jev.grep", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(repo))
    calls = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "semantic_search_code",
                               "arguments": {"query": "where is run?"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "semantic_search_code",
                               "arguments": {"query": "where is run?",
                                             "scope": ["private/secret_app.py"]}}}),
    ]
    stdout, stderr = process.communicate("\n".join(calls) + "\n", timeout=120)
    assert process.returncode == 0
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    by_id = {resp.get("id"): resp for resp in responses if "id" in resp}

    # Call 2, no scope: the startup anchor (outer) still answers, unchanged.
    outcome_2 = json.loads(by_id[2]["result"]["content"][0]["text"])
    assert outcome_2["error"]["code"] == "REMOTE_DISABLED"

    # Call 3, scope names the nested subtree: the more specific profile wins,
    # even though the process is already bound to the outer one.
    outcome_3 = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert outcome_3["error"]["code"] == "CREDENTIAL_MISSING"
