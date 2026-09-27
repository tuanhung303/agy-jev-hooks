"""Offline tests for the Grep PostToolUse filter: real profile resolution,
inventory and engine, with a scripted provider in place of Jev."""
import importlib.util
import json
from pathlib import Path

import pytest

from mcp.jev.grep import engine as engine_module
from mcp.jev.grep.config import default_configuration, dump_configuration_yaml
from mcp.jev.grep.jev import BatchEvaluation, ProviderUsage, build_request_payload, serialize_payload

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("claude_grep_filter", REPO / "hooks/claude-grep-filter.py")
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


class KeywordProvider:
    """Scores a fragment high when its path contains the keyword; records what was sent."""

    model = "jev-1.13.0"

    def __init__(self, keyword):
        self.keyword = keyword
        self.sent_paths = set()

    def serialize_batch(self, batch):
        return serialize_payload(build_request_payload(batch, self.model))

    def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
        self.sent_paths.update(item.path for item in batch.items)
        scores = {item.id: (0.9 if self.keyword in item.path else 0.1) for item in batch.items}
        return BatchEvaluation(scores, (), ProviderUsage(20, 4), self.model, self.model, 128, "req")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "tmp").mkdir()
    for index in range(9):
        (root / "docs" / f"note{index}.md").write_text(f"Enterprise note {index}\n", encoding="utf-8")
    (root / "src" / "rules.py").write_text(
        "def excluded(campaign):\n    return campaign in ('Enterprise', 'Partner')\n", encoding="utf-8")
    (root / "src" / "unrelated.py").write_text("def other():\n    return 1\n", encoding="utf-8")
    (root / "tmp" / "scratch.py").write_text("ENTERPRISE = 1\n", encoding="utf-8")
    config = default_configuration(str(root))
    config["remote_evaluation_enabled"] = True
    config["source"]["extra_deny_globs"] = ["tmp/**"]
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "repo.yaml").write_text(dump_configuration_yaml(config), encoding="utf-8")
    monkeypatch.setenv("JEVGREP_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("JEVGREP_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-only")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hook, "MODE", "filter")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(entry) for entry in [
        {"type": "user", "message": {"content": "where are Enterprise campaigns excluded from spend?"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll grep for Enterprise."},
            {"type": "tool_use", "id": "tool-1", "name": "Grep", "input": {}}]}},
    ]) + "\n", encoding="utf-8")
    return root, transcript


@pytest.fixture()
def provider(monkeypatch):
    scripted = KeywordProvider("rules")
    real = engine_module.SearchEngine

    class ScriptedEngine(real):
        def __init__(self, loaded, env=None, **kwargs):
            super().__init__(loaded, env=env, provider=scripted)

    monkeypatch.setattr(engine_module, "SearchEngine", ScriptedEngine)
    return scripted


def _payload(root, transcript, mode="files_with_matches", **tool_input):
    names = sorted([f"docs/note{i}.md" for i in range(9)] + ["src/rules.py", "tmp/scratch.py"])
    response = ({"mode": mode, "filenames": names, "numFiles": len(names)} if mode == "files_with_matches"
                else {"mode": mode, "numFiles": 0, "filenames": [], "numLines": len(names),
                      "content": "\n".join(f"{name}:1:Enterprise" for name in names)})
    return {"session_id": "sess", "tool_use_id": "tool-1", "transcript_path": str(transcript),
            "cwd": str(root), "tool_name": "Grep",
            "tool_input": {"pattern": "Enterprise", "-i": True, **tool_input}, "tool_response": response}


def test_filters_low_relevance_files_and_keeps_unevaluated_ones(repo, provider):
    root, transcript = repo
    output, event = hook.run(_payload(root, transcript))
    specific = output["hookSpecificOutput"]
    assert event["outcome"] == "filtered"
    # rules.py scored relevant; tmp/ was never sent (profile deny) so it stays visible.
    assert specific["updatedToolOutput"]["filenames"] == ["src/rules.py", "tmp/scratch.py"]
    assert specific["updatedToolOutput"]["numFiles"] == 2
    assert "kept 1 of 10 evaluated files" in specific["additionalContext"]
    assert "repeat the identical Grep call" in specific["additionalContext"]
    assert not any(path.startswith("tmp/") for path in provider.sent_paths)
    assert "src/unrelated.py" not in provider.sent_paths  # no match: never a candidate


def test_content_mode_keeps_the_output_shape(repo, provider):
    root, transcript = repo
    output, _ = hook.run(_payload(root, transcript, mode="content", **{"-n": True}))
    updated = output["hookSpecificOutput"]["updatedToolOutput"]
    assert set(updated) == {"mode", "numFiles", "filenames", "numLines", "content"}
    assert updated["content"].split("\n") == ["src/rules.py:1:Enterprise", "tmp/scratch.py:1:Enterprise"]
    assert updated["numLines"] == 2


def test_identical_repeat_is_unfiltered(repo, provider):
    root, transcript = repo
    assert hook.run(_payload(root, transcript))[1]["outcome"] == "filtered"
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "repeat_unfiltered"


def test_parallel_identical_twin_stays_unfiltered(repo, provider):
    root, transcript = repo
    assert hook._claim(_payload(root, transcript))  # the twin already holds the claim
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "repeat_unfiltered"


def test_unfiltered_call_does_not_arm_the_repeat_bypass(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MAX_BYTES", 10)
    assert hook.run(_payload(root, transcript))[1]["outcome"] == "search_rejected"
    monkeypatch.setattr(hook, "MAX_BYTES", 600000)
    assert hook.run(_payload(root, transcript))[1]["outcome"] == "filtered"


def test_too_broad_passes_through_before_any_preparation(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MAX_FILES", 5)
    output, event = hook.run(_payload(root, transcript))
    assert event["outcome"] == "too_broad" and "updatedToolOutput" not in output["hookSpecificOutput"]
    assert "narrower path" in output["hookSpecificOutput"]["additionalContext"]
    assert provider.sent_paths == set()


def test_long_scope_lists_fit_the_report_budget(repo, provider):
    root, transcript = repo
    deep = root / "src" / ("very_long_directory_name_" * 4)
    for index in range(30):
        folder = deep / f"module_{index:02d}_with_a_long_descriptive_name"
        folder.mkdir(parents=True)
        (folder / "rules_enterprise_handler.py").write_text("ENTERPRISE = True\n", encoding="utf-8")
    output, event = hook.run(_payload(root, transcript, path="src"))
    assert event["outcome"] == "filtered", json.dumps(event)


def test_few_files_and_shadow_mode_leave_output_alone(repo, provider, monkeypatch):
    root, transcript = repo
    output, event = hook.run(_payload(root, transcript, path="src"))
    assert output is None and event["outcome"] == "few_files"
    monkeypatch.setattr(hook, "MODE", "shadow")
    output, event = hook.run(_payload(root, transcript, glob="*"))
    assert output is None and event["outcome"] == "shadow_would_filter"
    output, event = hook.run(_payload(root, transcript, glob="*.md"))
    assert output is None and event["outcome"] == "none_relevant"


def test_over_budget_search_fails_open_without_sending(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MAX_BYTES", 10)
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "search_rejected"
    assert provider.sent_paths == set()


def test_main_fails_open_on_errors(monkeypatch, capsys):
    monkeypatch.setattr(hook, "run", lambda payload: 1 / 0)
    monkeypatch.setattr(hook, "_log", lambda event: None)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"tool_name": "Grep"})))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_task_comes_from_the_last_real_prompt(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(entry) for entry in [
        {"type": "user", "message": {"content": "old question"}},
        {"type": "user", "message": {"content": "<system-reminder>x</system-reminder>new question"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "noise"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "grepping now"},
                                                      {"type": "tool_use", "id": "t1"}]}},
    ]), encoding="utf-8")
    assert hook.task_from_transcript(str(transcript), "t1") == ("new question", "grepping now")


def test_cover_respects_the_scope_entry_limit():
    paths = [f"a/b{i}/f.py" for i in range(40)] + ["c/d.py"]
    covered = hook.cover(paths)
    assert len(covered) <= 32 and covered == ["a", "c"]
    assert hook.cover(["x.py", "y/z.py"]) == ["x.py", "y/z.py"]
    long_paths = [f"{'d' * 150}/m{i}/f.py" for i in range(30)]
    assert hook.cover(long_paths) == ["d" * 150]


def test_query_is_bounded_in_utf8_bytes():
    query = hook.build_query("tại sao " * 2000, "", "x")
    assert len(query.encode("utf-8")) <= 8192
