"""Offline tests for the Grep / Bash PostToolUse hint: real profile resolution,
inventory and engine, with a scripted provider in place of Jev. Tests of the
retired hide mode live in archive/grep-filter-hide-mode/."""
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
    monkeypatch.setattr(hook, "MODE", "shadow")
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


def test_ranks_only_matched_files_the_profile_allows(repo, provider):
    root, transcript = repo
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "shadow_ranked"
    assert event["ranked"][0] == ["src/rules.py", 0.9]
    assert event["kept_files"] == 1 and event["low_files"] == 9
    assert not any(path.startswith("tmp/") for path in provider.sent_paths)  # profile deny: never sent
    assert "src/unrelated.py" not in provider.sent_paths  # no match: never a candidate


def test_content_mode_is_ranked_too(repo, provider):
    root, transcript = repo
    _, event = hook.run(_payload(root, transcript, mode="content", **{"-n": True}))
    assert event["outcome"] == "shadow_ranked" and event["ranked"][0] == ["src/rules.py", 0.9]


def test_too_broad_passes_through_before_any_preparation(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MAX_FILES", 5)
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "too_broad"
    assert provider.sent_paths == set()


def test_long_scope_lists_fit_the_report_budget(repo, provider):
    root, transcript = repo
    deep = root / "src" / ("very_long_directory_name_" * 4)
    for index in range(30):
        folder = deep / f"module_{index:02d}_with_a_long_descriptive_name"
        folder.mkdir(parents=True)
        (folder / "enterprise_handler.py").write_text("ENTERPRISE = True\n", encoding="utf-8")
    output, event = hook.run(_payload(root, transcript, path="src"))
    assert event["outcome"] == "shadow_ranked", json.dumps(event)


def test_few_files_are_not_ranked_and_shadow_never_hints(repo, provider, monkeypatch):
    root, transcript = repo
    output, event = hook.run(_payload(root, transcript, path="src"))
    assert output is None and event["outcome"] == "few_files"
    output, event = hook.run(_payload(root, transcript, glob="*"))
    assert output is None and event["outcome"] == "shadow_ranked"
    assert event["ranked"][0] == ["src/rules.py", 0.9] and event["low_files"] == 9
    output, event = hook.run(_payload(root, transcript, glob="*.md"))
    assert output is None and event["outcome"] == "shadow_ranked" and event["kept_files"] == 0


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


def test_task_comes_from_the_recent_real_prompts(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(entry) for entry in [
        {"type": "user", "message": {"content": "too old"}},
        {"type": "user", "message": {"content": "oldest question"}},
        {"type": "user", "message": {"content": "old question"}},
        {"type": "user", "message": {"content": "<system-reminder>x</system-reminder>new question"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "noise"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "grepping now"},
                                                      {"type": "tool_use", "id": "t1"}]}},
    ]), encoding="utf-8")
    # The last three prompts, newest last: a bare "yes, do it" keeps the ones that say what the work is.
    # Assistant text is left out: at hook time the text that led to this call is not written yet.
    assert hook.task_from_transcript(str(transcript)) == ["oldest question", "old question", "new question"]
    assert hook.task_from_transcript(str(tmp_path / "missing.jsonl")) == []


def test_query_tags_each_request_oldest_first():
    query = hook.build_query(["first ask\n\nsecond paragraph", "yes do it"], "cache", "Find the cache key")
    assert query == ('<recent_user_requests order="oldest first">\n'
                     '<request n="1">first ask\n\nsecond paragraph</request>\n<request n="2">yes do it</request>\n'
                     '</recent_user_requests>\n<search_purpose>Find the cache key</search_purpose>\n'
                     '<grep_pattern>cache</grep_pattern>\n'
                     '<question>Which code matching the grep pattern is relevant to this task?</question>')
    assert hook.build_query([], "x").startswith("<grep_pattern>x</grep_pattern>")


def test_cover_respects_the_scope_entry_limit():
    paths = [f"a/b{i}/f.py" for i in range(40)] + ["c/d.py"]
    covered = hook.cover(paths)
    assert len(covered) <= 32 and covered == ["a", "c"]
    assert hook.cover(["x.py", "y/z.py"]) == ["x.py", "y/z.py"]
    long_paths = [f"{'d' * 150}/m{i}/f.py" for i in range(30)]
    assert hook.cover(long_paths) == ["d" * 150]


def test_query_is_bounded_in_utf8_bytes():
    query = hook.build_query(["tại sao " * 2000] * 3, "x")
    assert len(query.encode("utf-8")) <= 8192


def test_count_mode_is_left_alone(repo, provider):
    root, transcript = repo
    payload = _payload(root, transcript)
    payload["tool_response"] = {"mode": "count", "numFiles": 11, "filenames": [], "content": "x:1",
                                "numMatches": 11}
    assert hook.run(payload) == (None, {"session": "sess", "mode": "shadow", "outcome": "not_applicable"})


def test_path_without_a_profile_is_left_alone(tmp_path, repo, provider):
    _, transcript = repo
    outside = tmp_path / "outside"
    outside.mkdir()
    for index in range(9):
        (outside / f"f{index}.py").write_text("Enterprise\n", encoding="utf-8")
    output, event = hook.run(_payload(outside, transcript))
    assert output is None and event["outcome"] == "no_profile"


def test_provider_failure_fails_open(repo, monkeypatch):
    root, transcript = repo
    real = engine_module.SearchEngine

    class Failing(KeywordProvider):
        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            raise OSError("network down")

    class FailingEngine(real):
        def __init__(self, loaded, env=None, **kwargs):
            super().__init__(loaded, env=env, provider=Failing("rules"))

    monkeypatch.setattr(engine_module, "SearchEngine", FailingEngine)
    events = []
    monkeypatch.setattr(hook, "_log", events.append)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(_payload(root, transcript))))
    assert hook.main() == 0
    assert events[-1]["outcome"] in ("search_partial", "error")


def test_nested_profile_owns_its_subtree(tmp_path, repo, provider):
    root, transcript = repo
    nested = root / "tmp" / "worktrees" / "wt"
    nested.mkdir(parents=True)
    for index in range(9):
        (nested / f"n{index}.md").write_text("Enterprise\n", encoding="utf-8")
    (nested / "rules_x.py").write_text("ENTERPRISE = 1\n", encoding="utf-8")
    config = default_configuration(str(root / "tmp" / "worktrees"))
    config["remote_evaluation_enabled"] = True
    (tmp_path / "config" / "worktrees.yaml").write_text(dump_configuration_yaml(config), encoding="utf-8")
    output, event = hook.run(_payload(root, transcript, path="tmp/worktrees/wt"))
    assert event["outcome"] == "shadow_ranked"
    # Scored under the nested profile: every path is relative to its root.
    assert "wt/rules_x.py" in provider.sent_paths
    assert all(path.startswith("wt/") for path in provider.sent_paths)


def test_paths_with_spaces_and_unicode(repo, provider):
    root, transcript = repo
    odd = root / "src" / "thư mục có dấu"
    odd.mkdir()
    (odd / "rules đặc biệt.py").write_text("ENTERPRISE = 1\n", encoding="utf-8")
    output, event = hook.run(_payload(root, transcript, path="src"))
    assert event["outcome"] in ("few_files",) or "src/thư mục có dấu/rules đặc biệt.py" in provider.sent_paths


def test_hint_skips_jev_when_every_match_is_already_on_screen(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MODE", "hint")
    # 11 files, all shown, fewer than HINT_MIN_FILES: no hint could come of it, so Jev is not called.
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "all_shown" and event["unseen_files"] == 0
    output, event = hook.run(_bash_payload(root, transcript, "rg -n -i Enterprise"))
    assert output is None and event["outcome"] == "all_shown"
    assert provider.sent_paths == set()


# Bash: plain rg / grep commands get the same hint.

def _bash_payload(root, transcript, command, stdout=None):
    names = sorted([f"docs/note{i}.md" for i in range(9)] + ["src/rules.py", "tmp/scratch.py"])
    if stdout is None:
        stdout = "".join(f"{name}:1:Enterprise\n" for name in names)
    return {"session_id": "sess", "tool_use_id": "tool-1", "transcript_path": str(transcript),
            "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "search"},
            "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}}


@pytest.mark.parametrize("command, args, paths", [
    ("rg -n Enterprise src", ["-e", "Enterprise"], ["src"]),
    ("rg -i Enterprise 2>/dev/null", ["-i", "-e", "Enterprise"], ["."]),
    ("rg -tpy -g '!tests/**' -C3 foo", ["-t", "py", "-g", "!tests/**", "-e", "foo"], ["."]),
    ("grep -rn 'a\\|b(c)' docs", ["--no-ignore", "--hidden", "-e", "a|b\\(c\\)"], ["docs"]),
    ("grep -rniE 'a|b' --include=*.py .", ["--no-ignore", "--hidden", "--ignore-case", "--glob", "*.py", "-e", "a|b"],
     ["."]),
    ("fgrep -rl a.b .", ["--no-ignore", "--hidden", "--fixed-strings", "-e", "a.b"], ["."]),
    ("rg -e one -e two src docs", ["-e", "one", "-e", "two"], ["src", "docs"]),
])
def test_bash_search_mirrors_plain_search_commands(tmp_path, command, args, paths):
    parsed_args, parsed_paths, cwd, _, truncated = hook.bash_search(command, str(tmp_path))
    assert truncated is False
    assert parsed_args == args
    assert parsed_paths == [str(tmp_path / path) if path != "." else str(tmp_path) for path in paths]
    assert cwd == str(tmp_path)


@pytest.mark.parametrize("command", [
    "ls -la", "rg foo | wc -l", "rg foo > out.txt", "rg foo; echo done", "rg -c foo", "rg -v foo",
    "grep -rh foo .", "rg -r bar foo", "rg --heading foo", "rg -I foo", "git grep foo", "rg 'unclosed",
    "cd src && rg foo && ls", "rg", "rg foo | head -5 | sort", "rg foo | grep -v bar",
])
def test_bash_search_leaves_anything_it_cannot_mirror(tmp_path, command):
    assert hook.bash_search(command, str(tmp_path)) is None


def test_bash_search_follows_a_leading_cd(tmp_path):
    (tmp_path / "sub dir").mkdir()
    _, paths, cwd, _, _ = hook.bash_search(f"cd '{tmp_path}/sub dir' && rg -n foo", "/")
    assert cwd == paths[0] == str(tmp_path / "sub dir")


def test_bash_rg_output_is_ranked(repo, provider):
    root, transcript = repo
    output, event = hook.run(_bash_payload(root, transcript, "rg -n -i Enterprise"))
    assert output is None and event["outcome"] == "shadow_ranked" and event["tool"] == "Bash"
    assert event["ranked"][0] == ["src/rules.py", 0.9]


def test_non_search_bash_is_not_logged(monkeypatch, capsys):
    logged = []
    monkeypatch.setattr(hook, "_log", logged.append)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": "a\n"}})))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {} and logged == []


def test_bash_output_paths_resolve_in_every_printed_form(tmp_path):
    a, old = str(tmp_path / "a.py"), str(tmp_path / "a.py-old")

    def shown(*lines):
        return hook.visible_files({"stdout": "\n".join(lines) + "\n"}, {a, old}, str(tmp_path))
    # A line of a.py-old never counts as a context line of a.py, whatever form the path takes.
    assert shown("a.py-old:1:x", "a.py-old-2-z:w", "--", "a.py-old") == {old}
    assert shown("./a.py-2-context") == shown(f"{a}:3:y") == shown("a.py") == {a}


def test_hint_mode_ranks_without_hiding(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MODE", "hint")
    # A long list: the order matters even though everything was shown.
    monkeypatch.setattr(hook, "HINT_MIN_FILES", 5)
    note, event = hook.run(_payload(root, transcript))
    assert event["outcome"] == "hinted"
    assert "src/rules.py:1-2 (0.90)" in note  # the best-scored lines, not just the file
    assert "9 more scored below" in note and "Nothing was hidden" in note


def test_hint_names_the_relevant_file_head_cut_off(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MODE", "hint")
    payload = _bash_payload(root, transcript, "rg -n -i Enterprise | head -2",
                            stdout="docs/note0.md:1:Enterprise note 0\ndocs/note1.md:1:Enterprise note 1\n")
    note, event = hook.run(payload)
    assert event["outcome"] == "hinted" and event["unseen_top"] == 1
    assert "src/rules.py:1-2 (0.90, not in your output)" in note


def test_no_hint_when_the_top_files_were_already_shown(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MODE", "hint")
    payload = _bash_payload(root, transcript, "rg -n -i Enterprise | head -2",
                            stdout="docs/note0.md:1:Enterprise note 0\nsrc/rules.py:1:def excluded\n")
    note, event = hook.run(payload)
    assert note is None and event["outcome"] == "hint_not_needed"
    assert event["unseen_files"] == 9 and event["unseen_top"] == 0


def test_hint_exits_2_with_the_note_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(hook, "run", lambda payload: ("Jev hint: x", {"outcome": "hinted"}))
    monkeypatch.setattr(hook, "_log", lambda event: None)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"tool_name": "Bash"})))
    assert hook.main() == 2  # asyncRewake hands exit-2 stderr to Claude
    captured = capsys.readouterr()
    assert captured.err == "Jev hint: x\n" and captured.out == ""
    monkeypatch.setattr(hook, "run", lambda payload: (None, {"outcome": "shadow_ranked"}))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"tool_name": "Bash"})))
    assert hook.main() == 0  # no hint: a silent, successful exit


def test_bash_description_states_the_search_purpose(repo, monkeypatch):
    root, transcript = repo
    seen = []
    real = engine_module.SearchEngine

    class Recording(real):
        def search(self, request, invocation=None):
            seen.append(request["query"])
            return super().search(request, invocation)

        def __init__(self, loaded, env=None, **kwargs):
            super().__init__(loaded, env=env, provider=KeywordProvider("rules"))

    monkeypatch.setattr(engine_module, "SearchEngine", Recording)
    payload = _bash_payload(root, transcript, "rg -n -i Enterprise")
    payload["tool_input"]["description"] = "Find where Enterprise campaigns are excluded"
    _, event = hook.run(payload)
    assert "<search_purpose>Find where Enterprise campaigns are excluded</search_purpose>" in seen[0]
    assert '<request n="1">where are Enterprise' in seen[0]
    assert "I'll grep for Enterprise" not in seen[0]  # assistant text is stale at hook time
    assert event["intent"] == "description" and event["tool_use_id"] == "tool-1"


def test_snippets_keep_only_the_lines_around_matches():
    from mcp.jev.grep.chunker import PreparedFragment
    text = "".join(f"line {n}\n" for n in range(10, 60))
    fragment = PreparedFragment(id="f", path="a.py", sha256="x", start_line=10, end_line=59, byte_start=100,
                                byte_end=100 + len(text), text=text, byte_count=len(text), token_count=0,
                                chunker="c")
    trim = hook.snippet_mapper({"a.py": {30, 33}}, 2)
    snippet = trim(fragment)
    assert (snippet.start_line, snippet.end_line) == (28, 35)
    assert snippet.text == "".join(f"line {n}\n" for n in range(28, 36))
    assert snippet.byte_start == 100 + len("".join(f"line {n}\n" for n in range(10, 28)))
    assert snippet.byte_count == len(snippet.text) and snippet.token_count > 0
    assert snippet.label == "grep matches on lines 30, 33"
    assert trim(fragment) is None  # an overlapping twin with the same matches is scored once
    assert hook.snippet_mapper({"a.py": {5}}, 2)(fragment) is None  # no match inside: not a candidate


@pytest.mark.parametrize("command", [
    "rg -n foo | head", "rg -n foo | head -20", "grep -rn foo . 2>/dev/null | head -n 30",
    "rg foo 2>/dev/null|head --lines=5",
])
def test_bash_search_accepts_a_trailing_head(tmp_path, command):
    args, _, _, pattern, truncated = hook.bash_search(command, str(tmp_path))
    assert pattern == "foo" and truncated is True and args[-2:] == ["-e", "foo"]


def test_head_cut_output_is_ranked_even_when_small(repo, provider, monkeypatch):
    root, transcript = repo
    output, event = hook.run(_bash_payload(root, transcript, "rg -n -i Enterprise | head -3",
                                           stdout="docs/note0.md:1:Enterprise\n"))
    assert output is None and event["outcome"] == "shadow_ranked" and event["truncated"] is True
    assert event["ranked"][0] == ["src/rules.py", 0.9]


def test_subagent_calls_read_the_agent_transcript(repo, provider, tmp_path):
    root, transcript = repo
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({"type": "user", "message": {"content": "main session chat"}}) + "\n")
    agent_file = tmp_path / "session" / "subagents" / "agent-abc123.jsonl"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(json.dumps({"type": "user", "message": {"content": "Where are Enterprise rules?"}}) + "\n")
    payload = {**_payload(root, transcript), "transcript_path": str(session), "agent_id": "abc123",
               "agent_type": "Explore"}
    assert hook.agent_transcript(payload) == str(agent_file)
    _, event = hook.run(payload)
    assert event["transcript"] == str(agent_file) and event["agent_type"] == "Explore"
    assert hook.agent_transcript({**payload, "agent_id": "../x"}) == str(session)
    assert hook.agent_transcript({**payload, "agent_id": "missing"}) == str(session)


def test_nothing_sendable_is_not_reported_as_ranked(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "snippet_mapper", lambda lines, radius: (lambda fragment: None))
    output, event = hook.run(_payload(root, transcript))
    assert output is None and event["outcome"] == "nothing_evaluated"


def test_paths_outside_the_working_directory_are_shown_absolute(tmp_path):
    assert hook._shown(str(tmp_path / "a" / "b.py"), str(tmp_path)) == "a/b.py"
    assert hook._shown(str(tmp_path / "a" / "b.py"), str(tmp_path / "other")) == str(tmp_path / "a" / "b.py")


def test_snippets_are_capped_per_file():
    from mcp.jev.grep.chunker import PreparedFragment

    def fragment(start):
        text = "".join(f"line {n}\n" for n in range(start, start + 10))
        return PreparedFragment(id=f"f{start}", path="a.py", sha256="x", start_line=start, end_line=start + 9,
                                byte_start=0, byte_end=len(text), text=text, byte_count=len(text),
                                token_count=0, chunker="c")
    lines = {"a.py": {5, 15, 25}}
    capped = hook.snippet_mapper(lines, 1, per_file=2)
    assert [capped(fragment(s)) is not None for s in (1, 11, 21)] == [True, True, False]
    uncapped = hook.snippet_mapper(lines, 1, per_file=0)
    assert all(uncapped(fragment(s)) is not None for s in (1, 11, 21))


def _turn(tmp_path, *entries):
    path = tmp_path / "turn.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    return str(path)


CALL = {"type": "assistant", "message": {"stop_reason": "tool_use",
                                         "content": [{"type": "tool_use", "id": "tool-1", "name": "Bash"}]}}
RESULT = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1"}]}}


def test_turn_ended_reads_the_turn_end_after_the_call(tmp_path):
    working = _turn(tmp_path, CALL, RESULT, {"type": "assistant", "message": {"stop_reason": None}})
    assert not hook.turn_ended(working, "tool-1")
    main_done = _turn(tmp_path, CALL, RESULT, {"type": "system", "subtype": "turn_duration"})
    assert hook.turn_ended(main_done, "tool-1")
    agent_done = _turn(tmp_path, CALL, RESULT, {"type": "assistant", "message": {"stop_reason": "end_turn"}})
    assert hook.turn_ended(agent_done, "tool-1")
    earlier_end = _turn(tmp_path, {"type": "system", "subtype": "turn_duration"}, CALL, RESULT)
    assert not hook.turn_ended(earlier_end, "tool-1")  # an end before the call is the previous turn
    assert not hook.turn_ended(_turn(tmp_path, {"type": "user"}), "tool-1")  # call not flushed yet
    assert not hook.turn_ended(None, "tool-1") and not hook.turn_ended(main_done, None)


def test_hint_after_the_turn_ended_is_dropped(repo, provider, monkeypatch):
    root, transcript = repo
    monkeypatch.setattr(hook, "MODE", "hint")
    monkeypatch.setattr(hook, "HINT_MIN_FILES", 5)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "system", "subtype": "turn_duration"}) + "\n")
    note, event = hook.run(_payload(root, transcript))
    assert note is None and event["outcome"] == "late_dropped"
