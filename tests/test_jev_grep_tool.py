"""Offline tests for the jev_grep MCP tool: real rg, config and engine, a scripted provider in place of Jev."""
import io
import json
import threading
import time

import pytest

from mcp.jev.grep import engine as engine_module
from mcp.jev.grep import grep_server, grep_tool
from mcp.jev.grep.config import default_configuration, dump_configuration_yaml
from mcp.jev.grep.grep_rank import rg_matches
from mcp.jev.grep.jev import BatchEvaluation, ProviderError, ProviderUsage, build_request_payload, serialize_payload


class KeywordProvider:
    """Scores a fragment high when its path contains the keyword; records what was sent."""

    model = "jev-1.13.0"

    def __init__(self, keyword, fail=False, delay=0.0):
        self.keyword, self.fail, self.delay = keyword, fail, delay
        self.sent_paths = set()

    def serialize_batch(self, batch):
        return serialize_payload(build_request_payload(batch, self.model))

    def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
        self.sent_paths.update(item.path for item in batch.items)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise ProviderError("PROVIDER_UNAVAILABLE", "scripted outage", False, False)
        scores = {item.id: (0.9 if self.keyword in item.path else 0.1) for item in batch.items}
        return BatchEvaluation(scores, (), ProviderUsage(20, 4), self.model, self.model, 128, "req")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    for index in range(6):
        (root / "docs" / f"note{index}.md").write_text(f"intro\nEnterprise note {index}\n", encoding="utf-8")
    (root / "src" / "rules.py").write_text(
        "def excluded(campaign):\n    return campaign in ('Enterprise', 'Partner')\n", encoding="utf-8")
    config = default_configuration("/placeholder")
    config["remote_evaluation_enabled"] = True
    text = "\n".join(line for line in dump_configuration_yaml(config).splitlines()
                     if not line.startswith("repository_root"))
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "defaults.yaml").write_text(text + "\n", encoding="utf-8")
    monkeypatch.setenv("JEVGREP_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("JEVGREP_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    return root


@pytest.fixture()
def provider(monkeypatch):
    scripted = KeywordProvider("rules")
    real = engine_module.SearchEngine

    class ScriptedEngine(real):
        def __init__(self, loaded, env=None, **kwargs):
            super().__init__(loaded, env=env, provider=scripted)

    monkeypatch.setattr(engine_module, "SearchEngine", ScriptedEngine)
    return scripted


def call(root, **arguments):
    return grep_tool.run({"task": "where are Enterprise campaigns excluded?", **arguments}, str(root))


def test_ranked_reply_gives_code_for_the_relevant_file_and_lists_the_rest(repo, provider):
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "ranked" and event["matched_files"] == 7
    lines = text.splitlines()
    assert lines[0] == f"root: {repo}"
    assert "7 files match 'Enterprise'. Ranked for your task, best first." in text
    assert "src/rules.py:1-2 (matches: 2)\n```\n1  def excluded(campaign):" in text
    listed = text.split("Other matching files (file: match lines):\n")[1].splitlines()
    assert sorted(listed) == [f"docs/note{i}.md: 2" for i in range(6)]  # low scores: listed, no code
    assert str(repo) not in text.split("\n", 1)[1]  # the root is printed once


def test_small_result_is_plain_rg_output_without_jev(repo, provider):
    text, event = call(repo, pattern="Partner")
    assert event["outcome"] == "small" and not provider.sent_paths
    assert text.endswith("src/rules.py:2:    return campaign in ('Enterprise', 'Partner')")


def test_single_file_path_keeps_the_file_name(repo, provider):
    text, event = call(repo, pattern="Enterprise", path="src/rules.py")
    assert event["outcome"] == "small" and "src/rules.py:2:" in text


def test_bad_regex_returns_rg_error_not_no_matches(repo, provider):
    text, event = call(repo, pattern="(unclosed")
    assert event["outcome"] == "rg_error" and "rg error:" in text and "regex" in text.lower()


def test_no_match_says_so(repo, provider):
    text, event = call(repo, pattern="Nowhere", path="docs")
    assert event["outcome"] == "none" and "No matches for 'Nowhere' under docs." in text


def test_too_broad_lists_and_asks_to_narrow(repo, provider, monkeypatch):
    monkeypatch.setattr(grep_tool, "MAX_FILES", 5)
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "too_broad" and "narrow the pattern, path or glob" in text
    assert not provider.sent_paths


def test_jev_failure_falls_back_to_match_count_order(repo, provider):
    provider.fail = True
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "unranked" and "Unranked (" in text
    assert text.count("```\n") == 6  # three code blocks: the files with the most matches
    assert all(f"docs/note{i}.md" in text for i in range(6)) and "src/rules.py" in text


def test_missing_defaults_file_is_unranked_not_an_error(repo, provider, tmp_path, monkeypatch):
    monkeypatch.setenv("JEVGREP_CONFIG_HOME", str(tmp_path / "empty"))
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "unranked" and "no Jev configuration" in text and not provider.sent_paths


def test_credential_in_the_task_is_never_sent(repo, provider):
    text, event = grep_tool.run({"pattern": "Enterprise", "task": "token ghp_" + "a" * 36}, str(repo))
    assert event["outcome"] == "unranked" and "credential" in text and not provider.sent_paths


def test_deadline_keeps_what_was_scored(repo, provider, monkeypatch):
    provider.delay = 2.0
    monkeypatch.setattr(grep_tool, "BUDGET_S", 1.0)
    started = time.monotonic()
    text, event = call(repo, pattern="Enterprise")
    assert time.monotonic() - started < 1.8  # the budget bounds the call, not the provider
    assert event["outcome"] == "unranked" and "src/rules.py" in text


def test_relative_path_resolves_against_the_base_and_absolute_path_wins(repo, provider, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "x.py").write_text("Enterprise = 1\n", encoding="utf-8")
    text, _ = grep_tool.run({"pattern": "Enterprise", "task": "t", "path": str(other)}, str(repo))
    assert text.startswith(f"root: {other}") and "x.py:1:" in text
    with pytest.raises(grep_tool.ToolInputError, match="path not found"):
        grep_tool.run({"pattern": "x", "task": "t", "path": "missing"}, str(repo))
    with pytest.raises(grep_tool.ToolInputError, match="task is required"):
        grep_tool.run({"pattern": "x", "task": " "}, str(repo))


def test_rg_matches_reports_errors_and_names_single_files(tmp_path):
    (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
    found, error, complete = rg_matches(["-e", "hello"], [str(tmp_path / "a.py")])
    assert list(found) == [str(tmp_path / "a.py")] and found[str(tmp_path / "a.py")] == {1: "hello"}
    assert error is None and complete
    found, error, _ = rg_matches(["-e", "(bad"], [str(tmp_path)])
    assert not found and error


def _serve(messages, run):
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    out, events = io.StringIO(), []
    grep_server.serve(stdin, out, base="/base", run=run, log=events.append)
    return [json.loads(line) for line in out.getvalue().splitlines()], events


def test_server_lists_the_tool_and_runs_calls_at_once():
    gate, running = threading.Barrier(2, timeout=5), []

    def run(arguments, base, cancel_event=None, started=None):
        running.append(arguments["pattern"])
        gate.wait()  # both calls must be in flight together: no queue, no BUSY
        return f"{base}:{arguments['pattern']}", {"outcome": "ranked"}
    replies, events = _serve([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "jev_grep", "arguments": {"pattern": "a"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "jev_grep", "arguments": {"pattern": "b"}}},
    ], run)
    by_id = {reply["id"]: reply for reply in replies}
    assert by_id[2]["result"]["tools"][0]["name"] == "jev_grep"
    assert by_id[3]["result"]["content"][0]["text"] == "/base:a"
    assert by_id[4]["result"]["content"][0]["text"] == "/base:b"
    assert len(events) == 2 and all(event["base"] == "/base" for event in events)


def test_server_turns_bad_input_into_a_tool_error_and_drops_cancelled_calls():
    def run(arguments, base, cancel_event=None, started=None):
        if arguments.get("slow"):
            cancel_event.wait(5)
            return "late", {"outcome": "ranked"}
        raise grep_tool.ToolInputError("pattern is required")

    stdin_lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "jev_grep", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "jev_grep", "arguments": {"slow": True}}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}},
    ]
    replies, _ = _serve(stdin_lines, run)
    assert [reply["id"] for reply in replies] == [1]
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"][0]["text"] == "pattern is required"


def test_base_folder_prefers_explicit_roots(tmp_path):
    assert grep_server.base_folder({"JEV_GREP_ROOT": str(tmp_path)}) == str(tmp_path.resolve())
    assert grep_server.base_folder({"CLAUDE_PROJECT_DIR": str(tmp_path)}) == str(tmp_path.resolve())


def test_cli_grep_prints_the_same_reply(repo, provider, monkeypatch, capsys, tmp_path):
    from mcp.jev.grep import cli
    monkeypatch.setattr(grep_server, "STATE_DIR", tmp_path / "state")
    monkeypatch.chdir(repo)
    assert cli.main(["grep", "Enterprise", "--task", "where are Enterprise campaigns excluded?"]) == 0
    assert capsys.readouterr().out.strip() == call(repo, pattern="Enterprise")[0]
    assert cli.main(["grep", "x", "missing", "--task", "t"]) == 2
