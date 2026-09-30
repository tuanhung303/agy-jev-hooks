"""Offline tests for the jev_grep MCP tool: real rg, config and engine, a scripted provider in place of Jev."""
import io
import json
import subprocess
import threading
import time

import pytest

from mcp.jev.grep import engine as engine_module
from mcp.jev.grep import grep_server, grep_tool
from mcp.jev.grep.config import default_configuration, dump_configuration_yaml
from mcp.jev.grep.chunker import PreparedFragment
from mcp.jev.grep.grep_rank import rg_matches, snippet_mapper
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
    assert "7 files match. Ranked." in text
    assert "src/rules.py:1-2 (matches: 2)\n```\n1  def excluded(campaign):" in text
    listed = text.split("Other matches:\n")[1].splitlines()
    assert sorted(listed) == [f"docs/note{i}.md: 2" for i in range(6)]  # low scores: listed, no code
    assert str(repo) not in text.split("\n", 1)[1]  # the root is printed once


def test_small_result_is_plain_rg_output_without_jev(repo, provider):
    text, event = call(repo, pattern="Partner")
    assert event["outcome"] == "small" and not provider.sent_paths
    assert text.endswith("src/rules.py:2:    return campaign in ('Enterprise', 'Partner')")


def test_single_file_path_keeps_the_file_name(repo, provider):
    text, event = call(repo, pattern="Enterprise", path="src/rules.py")
    assert event["outcome"] == "small" and "1 file match 'Enterprise'" not in text
    assert "src/rules.py:2:" in text


def test_bad_regex_returns_rg_error_not_no_matches(repo, provider):
    text, event = call(repo, pattern="(unclosed")
    assert event["outcome"] == "rg_error" and "rg error:" in text and "regex" in text.lower()


def test_no_match_says_so(repo, provider):
    text, event = call(repo, pattern="Nowhere", path="docs")
    assert event["outcome"] == "none" and "No matches." in text


def test_disappeared_text_file_is_unreadable_not_binary(repo, provider, monkeypatch):
    path = repo / "src" / "vanished.py"
    path.write_text("Enterprise vanished\n", encoding="utf-8")
    real = grep_tool.rg_matches

    def scan(*args, **kwargs):
        result = real(*args, **kwargs)
        path.unlink()
        return result

    monkeypatch.setattr(grep_tool, "rg_matches", scan)
    text, event = call(repo, pattern="Enterprise", path="src/vanished.py")
    assert event["outcome"] == "unreadable"
    assert "vanished.py (unreadable now)" in text and "(binary)" not in text


def test_file_disappearing_between_scans_is_unreadable(repo, provider, monkeypatch):
    path = repo / "src" / "vanished.py"
    path.write_text("Enterprise vanished\n", encoding="utf-8")
    real = grep_tool.rg_file_counts

    def scan(*args, **kwargs):
        counts, error, complete = real(*args, **kwargs)
        path.unlink()
        return counts, error, complete

    monkeypatch.setattr(grep_tool, "rg_file_counts", scan)
    text, event = call(repo, pattern="Enterprise", path="src/vanished.py")
    assert event["outcome"] == "unreadable"
    assert "vanished.py (unreadable now)" in text and "rg error:" not in text


@pytest.mark.parametrize("score_value", [0.1, 0.9])
def test_file_disappearing_during_ranking_is_unreadable(repo, provider, monkeypatch, score_value):
    path = repo / "docs" / "note0.md"

    def score(request, root, matches, *args):
        path.unlink()
        return ({name: (score_value if name == "docs/note0.md" else (0.9 if name == "src/rules.py" else 0.1),
                        1, 2) for name in matches}, None)

    monkeypatch.setattr(grep_tool, "_score", score)
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "ranked"
    assert "note0.md (unreadable now)" in text
    assert "note0.md: 2" not in text


def test_file_disappearing_during_render_is_not_listed_as_match(repo, monkeypatch):
    path = repo / "docs" / "note0.md"
    real_status, calls = grep_tool._file_status, 0

    def status(name, check_binary=True):
        nonlocal calls
        if name == str(path) and (calls := calls + 1) == 2:
            path.unlink()
        return real_status(name, check_binary)

    monkeypatch.setattr(grep_tool, "_file_status", status)
    matches = {"src/rules.py": {1: "needle"}, "docs/note0.md": {2: "needle"}}
    text = grep_tool._render(str(repo), str(repo), "", "2 files match.", matches, list(matches),
                             {"src/rules.py": (0.9, 1, 1)}, None, 0.5)
    assert "note0.md (unreadable now)" in text
    assert "note0.md:" not in text.split("Other matches:\n")[-1]


def test_too_broad_lists_and_asks_to_narrow(repo, provider, monkeypatch):
    monkeypatch.setattr(grep_tool, "MAX_FILES", 5)
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "too_broad" and "Narrow with a path or glob" in text
    assert not provider.sent_paths


def test_jev_failure_falls_back_to_match_count_order(repo, provider):
    provider.fail = True
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "unranked" and "7 files match. Unranked (" in text and "; by match count." in text
    assert "ranking service error (PROVIDER_UNAVAILABLE)" in text, repr(event)
    assert text.count("```\n") == 6  # three code blocks: the files with the most matches
    assert all(f"docs/note{i}.md" in text for i in range(6)) and "src/rules.py" in text


def test_unscored_files_without_stop_reason_get_safe_exclusion_summary(repo, provider, monkeypatch):
    monkeypatch.setattr(grep_tool, "_score", lambda request, root, matches, *args:
                        ({"src/rules.py": (0.9, 1, 2)}, "not scorable (too large, binary, or excluded)"))
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "partly_ranked" and event["scored_files"] == 1
    assert "Scored 1 of 7; not scorable (too large, binary, or excluded)." in text


def test_missing_defaults_file_is_unranked_not_an_error(repo, provider, tmp_path, monkeypatch):
    monkeypatch.setenv("JEVGREP_CONFIG_HOME", str(tmp_path / "empty"))
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "unranked" and "ranking service unavailable" in text and not provider.sent_paths


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


def test_paths_use_caller_base_and_print_root_only_when_needed(repo, provider, tmp_path):
    nested = repo / "nested"
    nested.mkdir()
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    (nested / "x.py").write_text("Enterprise = 1\n", encoding="utf-8")
    text, _ = grep_tool.run({"pattern": "Enterprise", "task": "find config", "path": "nested"}, str(repo))
    assert text.startswith(f"root: {repo}")  # MCP always prints the server base.
    assert "nested/x.py:1:" in text
    cli_text, _ = grep_tool.run({"pattern": "Enterprise", "task": "find config", "path": "nested"},
                                str(repo), show_root=False)
    assert not cli_text.startswith("root:") and "nested/x.py:1:" in cli_text


def test_count_phase_parses_colon_paths_and_bounds_broad_search(repo):
    colon = repo / "docs" / "a:b.md"
    colon.write_text("needle\nneedle\n", encoding="utf-8")
    counts, error, complete = grep_tool.rg_file_counts(["-e", "needle"], ["docs"], cwd=str(repo))
    assert error is None and complete and counts[str(colon)] == 2


def test_rg_records_preserve_newline_and_colon_paths(repo, provider):
    odd = repo / "with\nnewline:a.py"
    odd.write_text("needle = 1\n", encoding="utf-8")
    counts, error, complete = grep_tool.rg_file_counts(["-e", "needle"], [str(odd)])
    matches, match_error, match_complete = grep_tool.rg_matches(["-e", "needle"], [str(odd)])
    assert error is match_error is None and complete and match_complete
    assert counts[str(odd)] == 1 and matches[str(odd)] == {1: "needle = 1"}
    text, event = call(repo, pattern="needle")
    assert event["matched_files"] == 1 and "with\nnewline:a.py:1:needle = 1" in text


def test_rg_config_cannot_color_counted_paths(repo, provider, tmp_path, monkeypatch):
    path = repo / "plain.py"
    path.write_text("needle\n", encoding="utf-8")
    config = tmp_path / "rg.conf"
    config.write_text("--color=always\n", encoding="utf-8")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    counts, error, complete = grep_tool.rg_file_counts(["-e", "needle"], [str(path)])
    assert error is None and complete and counts == {str(path): 1}


def test_jev_grep_ignores_ripgrep_config(repo, tmp_path, monkeypatch):
    path = repo / "plain.py"
    path.write_text("axb\na.b\n", encoding="utf-8")
    config = tmp_path / "rg.conf"
    config.write_text("--fixed-strings\n", encoding="utf-8")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    text, event = grep_tool.run({"pattern": "a.b", "task": "find matching lines", "path": "plain.py"}, str(repo))
    assert event["outcome"] == "small" and "plain.py:1:axb" in text and "plain.py:2:a.b" in text


def test_binary_matches_are_listed_and_never_sent_to_jev(repo, provider, monkeypatch, capsys):
    from mcp.jev.grep import cli
    binary = repo / "payload.py"
    binary.write_bytes(b"needle" + b"x" * 500 + b"\0tail\n")
    monkeypatch.chdir(repo)
    assert cli.main(["grep", "needle", "--task", "find matching source"]) == 0
    assert "payload.py (binary)" in capsys.readouterr().out
    assert provider.sent_paths == set()


def test_nul_anywhere_marks_file_binary_before_rendering_or_scoring(repo, provider):
    path = repo / "around-nul.py"
    path.write_bytes(b"needle = 1\n" + b"x\0y\n" + b"needle = 2\n")
    text, event = call(repo, pattern="needle")
    assert event["outcome"] == "binary"
    assert "around-nul.py (binary)" in text
    assert "needle =" not in text and provider.sent_paths == set()


def test_rg_exit_two_keeps_records_from_readable_paths(repo):
    valid = repo / "docs" / "readable.txt"
    valid.write_text("needle\n", encoding="utf-8")
    found, error, complete = grep_tool.rg_matches(["-e", "needle"], [str(valid), str(repo / "missing")])
    assert complete and error == "rg could not read some files." and found[str(valid)] == {1: "needle"}


def test_partial_rg_read_warning_survives_into_reply_and_event(repo, provider, monkeypatch):
    path = repo / "src" / "rules.py"
    monkeypatch.setattr(grep_tool, "rg_file_counts",
                        lambda *a, **k: ({str(path): 1}, "rg could not read some files.", True))
    text, event = call(repo, pattern="Enterprise")
    assert "rg could not read some files." in text
    assert event["rg_warning"] == "rg could not read some files."


def test_rg_line_output_is_bounded_separately_from_jev_payload(repo):
    long_line = repo / "long.js"
    long_line.write_text("x" * (2 << 20) + "needle\n", encoding="utf-8")
    counts, error, complete = grep_tool.rg_file_counts(["-e", "needle"], [str(long_line)], max_bytes=1 << 20)
    matches, match_error, match_complete = grep_tool.rg_matches(
        ["-e", "needle"], [str(long_line)], max_bytes=1 << 20)
    assert error is match_error is None and complete and match_complete
    assert counts[str(long_line)] == 1 and len(matches[str(long_line)][1]) <= 400


def test_empty_second_phase_is_none_and_cli_exit_one(repo, provider, monkeypatch, capsys):
    from mcp.jev.grep import cli
    monkeypatch.chdir(repo)
    monkeypatch.setattr(grep_tool, "rg_matches", lambda *args, **kwargs: ({}, None, True))
    assert cli.main(["grep", "Enterprise", "--task", "find it"]) == 1
    assert "No matches" in capsys.readouterr().out


def test_separator_stops_leading_dash_normalization(repo, provider, monkeypatch, capsys):
    from mcp.jev.grep import cli
    retry = repo / "-retry"
    retry.mkdir()
    (retry / "x.py").write_text('flag = "-e"\n', encoding="utf-8")
    monkeypatch.chdir(repo)
    assert cli.main(["grep", "--task", "find literal flag", "--", "-e", "-retry"]) == 0
    assert "-retry/x.py:1:" in capsys.readouterr().out


def test_too_broad_limits_first_component_groups_to_eight(repo, provider, monkeypatch):
    for index in range(10):
        folder = repo / f"folder{index}"
        folder.mkdir()
        (folder / "hit.py").write_text("needle\n", encoding="utf-8")
    (repo / "top.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(grep_tool, "MAX_FILES", 5)
    text, event = call(repo, pattern="needle")
    assert event["outcome"] == "too_broad"
    assert "+3 more folders" in text and text.count("/:" ) == 7
    assert "(top level): 1" in text and "Narrow with a path or glob." in text


def test_code_block_reads_only_requested_lines(repo, monkeypatch):
    path = repo / "large.py"
    path.write_text("".join(f"line {number}\n" for number in range(1, 10001)), encoding="utf-8")
    real_open = open
    reads = []

    class Tracked:
        def __init__(self, handle, record=False):
            self.handle = handle
            self.record = record

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __iter__(self):
            return iter(self.handle)

        def read(self, *args):
            if self.record:
                reads.append("read")
            return self.handle.read(*args)

    def tracked_open(name, *args, **kwargs):
        handle = real_open(name, *args, **kwargs)
        return Tracked(handle, str(name) == str(path))

    monkeypatch.setattr("builtins.open", tracked_open)
    block = grep_tool._code_block(str(repo), str(repo), "large.py", {5000}, (0.9, 4990, 5010))
    assert "line 5000" in block and reads == []


def test_too_broad_shows_folder_counts_and_no_file_list(repo, provider, monkeypatch):
    (repo / "root.py").write_text("Enterprise = 1\n", encoding="utf-8")
    monkeypatch.setattr(grep_tool, "MAX_FILES", 5)
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "too_broad" and "8 matching files" in text
    assert "docs/: 6" in text and "(top level): 1" in text and "root.py" not in text and not provider.sent_paths
    assert str(repo) not in text.split("\n", 1)[1]


def test_too_broad_partial_read_qualifies_count_and_keeps_warning(repo, provider, monkeypatch):
    monkeypatch.setattr(grep_tool, "MAX_FILES", 5)
    monkeypatch.setattr(grep_tool, "rg_file_counts", lambda *a, **k: (
        {f"/repo/f{i}.py": 1 for i in range(6)}, "rg could not read some files.", True))
    text, event = call(repo, pattern="Enterprise")
    assert "at least 6 matching files" in text
    assert "rg could not read some files." in text and event["rg_warning"]


def test_incomplete_count_or_match_phase_never_ranks_partial_files(repo, provider, monkeypatch):
    real_counts = grep_tool.rg_file_counts
    monkeypatch.setattr(grep_tool, "rg_file_counts", lambda *a, **k: ({"/repo/a.py": 1}, None, False))
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "too_broad" and "scan may be incomplete" in text
    assert "Nothing is hidden" not in text and not provider.sent_paths
    monkeypatch.setattr(grep_tool, "rg_file_counts", real_counts)
    monkeypatch.setattr(grep_tool, "rg_matches", lambda *a, **k: ({}, None, False))
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "too_broad" and "Match details exceeded" in text
    assert "7 matching files" in text and "scan may be incomplete" not in text
    assert not provider.sent_paths


def test_unscored_files_have_plain_cause_and_singular_plural(repo, provider):
    text, event = call(repo, pattern="Enterprise")
    assert event["scored_files"] == 7 and "Scored 7 of 7" not in text


def test_low_scores_show_keyword_match_lines(repo, provider):
    provider.keyword = "absent"
    text, event = call(repo, pattern="Enterprise")
    assert event["outcome"] == "ranked"
    assert "Best keyword matches, not judged relevant:" in text
    assert "docs/note0.md:2:Enterprise note 0" in text
    assert "docs/note0.md: 2" not in text.split("Other matches:\n", 1)[-1]
    assert "docs/note1.md: 2" not in text.split("Other matches:\n", 1)[-1]


def test_low_score_preview_reports_omitted_matching_lines(repo, provider):
    matches = {"docs/note0.md": {n: f"needle {n}" for n in range(1, 21)},
               "docs/note1.md": {n: f"needle {n}" for n in range(1, 11)}}
    scores = {path: (0.1, 1, 2) for path in matches}
    text = grep_tool._render(str(repo), str(repo), "", "2 files match.", matches, list(matches),
                             scores, None, 0.5)
    assert "docs/note0.md:1:needle 1" in text
    assert "+15 more" in text
    assert text.index("docs/note0.md: +15 more") < text.index("docs/note1.md:1:needle 1")
    assert "Other matches:" not in text


def test_code_block_shows_twenty_lines_and_other_match_lines(repo):
    path = repo / "wide.py"
    path.write_text("".join(f"line {n}\n" for n in range(1, 101)), encoding="utf-8")
    block = grep_tool._code_block(str(repo), str(repo), "wide.py", {5: "line 5", 50: "line 50", 99: "line 99"},
                                  (0.9, 1, 20))
    code = block.split("```\n", 1)[1].split("\n```", 1)[0]
    assert len(code.splitlines()) <= 20
    assert "also:\n50: line 50\n99: line 99" in block
    many = {n: f"line {n}" for n in range(1, 28)}
    block = grep_tool._code_block(str(repo), str(repo), "wide.py", many, (0.9, 1, 20))
    assert "wide.py:1-20 (27 matches)" in block
    assert "25: line 25\n+2 more" in block and "27: line 27" not in block
    assert "(matches: 1, 2" not in block and "+2 more" in block


def test_no_match_summary_omits_the_search_pattern(repo, provider):
    text, _ = call(repo, pattern=r"ref\(")
    assert text == f"root: {repo}\n\nNo matches."


def test_grep_last_fragment_is_opt_in_and_preserves_hook_cap():
    fragments = [PreparedFragment(str(start), "module.py", "hash", start, end, 0, 0,
                                  "".join(f"line {n}\n" for n in range(start, start + 10)), 0, 1, "test")
                 for start, end in ((1, 10), (81, 90), (280, 290), (285, 295))]
    def selected(mapper):
        return [fragment for item in fragments if (fragment := mapper(item)) is not None]
    lines = {"module.py": {5, 85, 286, 291}}
    assert len(selected(snippet_mapper(lines, 1))) == 2
    assert [fragment.start_line for fragment in selected(snippet_mapper(lines, 1, include_last=True))] == [4, 84, 285]


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
    assert capsys.readouterr().out.strip() == grep_tool.run(
        {"pattern": "Enterprise", "task": "where are Enterprise campaigns excluded?"}, str(repo), show_root=False)[0]
    assert cli.main(["grep", "x", "missing", "--task", "t"]) == 2
    event = json.loads((tmp_path / "state" / "events.jsonl").read_text().splitlines()[-1])
    assert event["outcome"] == "bad_input" and event["elapsed_s"] >= 0


def test_cli_regex_glob_exit_codes_and_leading_dash_pattern(repo, provider, monkeypatch, capsys, tmp_path):
    from mcp.jev.grep import cli
    monkeypatch.setattr(grep_server, "STATE_DIR", tmp_path / "state")
    monkeypatch.chdir(repo)
    (repo / "docs" / "retry.py").write_text("-retry = True\n", encoding="utf-8")
    assert cli.main(["grep", "-e", "-retry", ".", "--task", "find retry"]) == 0
    assert "docs/retry.py:1:" in capsys.readouterr().out
    assert cli.main(["grep", "docs", "-e", "Enterprise", "--task", "find notes"]) == 0
    assert "docs/note0.md:2:" in capsys.readouterr().out
    assert cli.main(["grep", "(", "--task", "bad regex"]) == 2
    assert "unclosed" in capsys.readouterr().out.lower()
    assert cli.main(["grep", "Enterprise", "--task", "filter", "--glob", "[",]) == 2
    assert "glob" in capsys.readouterr().out.lower()


def test_repeated_cli_globs_are_passed_as_a_list(repo, provider, monkeypatch, capsys, tmp_path):
    from mcp.jev.grep import cli
    monkeypatch.setattr(grep_server, "STATE_DIR", tmp_path / "state")
    monkeypatch.chdir(repo)
    (repo / "docs" / "note.json").write_text('{"note":"Enterprise"}\n', encoding="utf-8")
    assert cli.main(["grep", "Enterprise", "docs", "--task", "notes", "--glob", "*.md",
                     "--glob", "*.json"]) == 0
    output = capsys.readouterr().out
    assert "note.json" in output and "note0.md" in output


def test_no_ignore_searches_ignored_files_and_engine_scope(repo, provider):
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "rules.py").write_text("Enterprise = 1\n", encoding="utf-8")
    text, event = call(repo, pattern="Enterprise", no_ignore=True)
    assert event["matched_files"] == 8
    assert "ignored/rules.py" in text
    assert "ignored/rules.py" in provider.sent_paths


def test_server_marks_rg_errors_as_tool_errors():
    def run(arguments, base, cancel_event=None, started=None):
        return "rg error: bad regex", {"outcome": "rg_error", "elapsed_s": 0.1}
    replies, _ = _serve([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "jev_grep", "arguments": {"pattern": "["}}}], run)
    assert replies[0]["result"]["isError"] is True
