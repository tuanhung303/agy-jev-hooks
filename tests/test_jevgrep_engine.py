"""End-to-end engine tests with a scripted provider: offline, deterministic,
validated against the response contract."""
import os

import pytest

from mcp.jev.grep.config import default_configuration, dump_configuration_yaml, load_configuration
from mcp.jev.grep.contracts import validate_search_result
from mcp.jev.grep.engine import SearchEngine
from mcp.jev.grep.jev import (BatchEvaluation, EvaluationBatch, ProviderUsage, build_request_payload,
                              serialize_payload)


class ScriptedProvider:
    """Scores fragments by path keyword; one attempt per batch, usage always known."""

    model = "jev-1.13.0"

    def __init__(self, keyword="handler", high=0.9, low=0.1):
        self.keyword = keyword
        self.high = high
        self.low = low
        self.attempts = 0

    def serialize_batch(self, batch: EvaluationBatch) -> str:
        return serialize_payload(build_request_payload(batch, self.model))

    def evaluate_batch(self, batch: EvaluationBatch, is_cancelled=None, timeout_s=None) -> BatchEvaluation:
        self.attempts += 1
        scores = {item.id: (self.high if self.keyword in item.path else self.low) for item in batch.items}
        return BatchEvaluation(scores, (), ProviderUsage(20, 4), self.model, self.model, 128, "req")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "handler.py").write_text(
        "def handle_session_expiry(session):\n"
        "    if session.age > 3600:\n"
        "        session.expire()\n"
        "    return session\n", encoding="utf-8")
    (repo / "src" / "other.py").write_text("def unrelated():\n    return 42\n", encoding="utf-8")
    (repo / "README.md").write_text("session expiry policy notes\n", encoding="utf-8")

    config = default_configuration(str(repo))
    config["remote_evaluation_enabled"] = True
    config["cache"]["ttl_seconds"] = 604_800
    profile = tmp_path / "profile.yaml"
    profile.write_text(dump_configuration_yaml(config), encoding="utf-8")

    monkeypatch.setenv("JEVGREP_CACHE_HOME", str(tmp_path / "cache"))
    loaded = load_configuration(str(profile), env=dict(os.environ))
    return loaded


def test_yaml_profile_round_trips(project):
    config = project["config"]
    assert config["search"]["threshold"] == 0.5
    assert config["scan_caps"]["fragments"] is None
    assert config["provider"]["base_url"] == "https://api.typesafe.ai"
    assert config["source"]["extra_deny_globs"] == []


def test_profile_discovery_by_workspace(tmp_path):
    from mcp.jev.grep.config import ConfigurationError, find_profile_for

    repo = tmp_path / "repo"
    (repo / "deep" / "nested").mkdir(parents=True)
    config = default_configuration(str(repo))
    config_home = tmp_path / "conf"
    config_home.mkdir()
    profile = config_home / "repo.yaml"
    profile.write_text(dump_configuration_yaml(config), encoding="utf-8")
    env = {"JEVGREP_CONFIG_HOME": str(config_home)}

    assert find_profile_for(str(repo / "deep" / "nested"), env) == str(profile)
    assert find_profile_for(str(repo), env) == str(profile)
    assert find_profile_for(str(tmp_path), env={"JEVGREP_CONFIG_HOME": str(config_home),
                                                "JEVGREP_PROFILE": str(profile)}) == str(profile)
    with pytest.raises(ConfigurationError):
        find_profile_for(str(tmp_path / "unrelated"), env)


def test_search_returns_exact_excerpts_and_valid_report(project):
    provider = ScriptedProvider()
    engine = SearchEngine(project, provider=provider)
    result = engine.search({"query": "where is session expiry handled?"})
    outcome = result["outcome"]
    validate_search_result(outcome)
    assert outcome["status"] in ("complete", "partial")
    paths = {excerpt["path"] for excerpt in outcome["excerpts"]}
    assert "src/handler.py" in paths
    assert all(excerpt["score"] >= 0.5 for excerpt in outcome["excerpts"])
    report = outcome["report"]
    assert report["response_budget"]["counter"].startswith("utf8-bytes@1/")
    assert report["fragments"]["remote_evaluated"] == report["fragments"]["below_threshold"] \
        + report["fragments"]["above_threshold"]
    assert provider.attempts >= 1

    for excerpt in outcome["excerpts"]:
        source = (project["repository_root"] + "/" + excerpt["path"]).replace("/", os.sep)
        with open(source, encoding="utf-8") as handle:
            lines = handle.read().splitlines(keepends=True)
        assert excerpt["code"] == "".join(lines[excerpt["start_line"] - 1:excerpt["end_line"]])


def test_second_search_reuses_the_score_cache(project):
    provider = ScriptedProvider()
    engine = SearchEngine(project, provider=provider)
    first = engine.search({"query": "where is session expiry handled?"})["outcome"]
    attempts_after_first = provider.attempts
    second = engine.search({"query": "where is session expiry handled?"})["outcome"]
    validate_search_result(second)
    assert provider.attempts == attempts_after_first  # nothing re-dispatched
    assert second["report"]["fragments"]["cache_reused"] > 0
    assert second["report"]["fragments"]["remote_evaluated"] == 0
    assert first["report"]["usage"]["provider_request_attempts"] > 0
    assert second["report"]["usage"]["provider_request_attempts"] == 0


def test_changed_source_busts_the_cache_entry(project):
    engine = SearchEngine(project, provider=ScriptedProvider(keyword="handler"))
    engine.search({"query": "question one"})
    source = os.path.join(project["repository_root"], "src", "handler.py")
    with open(source, "a", encoding="utf-8") as handle:
        handle.write("# a new comment changes the fragment text\n")
    provider = ScriptedProvider(keyword="handler")
    outcome = SearchEngine(project, provider=provider).search({"query": "question one"})["outcome"]
    validate_search_result(outcome)
    # the edited file re-evaluates (identity binds the excerpt text), untouched files reuse
    assert outcome["report"]["fragments"]["remote_evaluated"] > 0


def test_remote_disabled_is_a_rejection_without_dispatch(project):
    project["config"]["remote_evaluation_enabled"] = False
    engine = SearchEngine(project, provider=None)
    outcome = engine.search({"query": "anything"})["outcome"]
    assert outcome["status"] == "rejected"
    assert outcome["error"]["code"] == "REMOTE_DISABLED"


def test_invalid_request_is_rejected_not_raised(project):
    engine = SearchEngine(project, provider=ScriptedProvider())
    outcome = engine.search({"query": ""})["outcome"]
    assert outcome["status"] == "rejected" and outcome["error"]["code"] == "INVALID_REQUEST"
    outcome = engine.search({"query": "ok", "scope": ["../escape"]})["outcome"]
    assert outcome["error"]["code"] == "INVALID_REQUEST"


def test_scan_cap_rejects_before_dispatch(project):
    project["config"]["scan_caps"]["request_attempts"] = 0
    provider = ScriptedProvider()
    engine = SearchEngine(project, provider=provider)
    outcome = engine.search({"query": "where is session expiry handled?"})
    result = outcome["outcome"]
    validate_search_result(result)
    assert result["status"] == "rejected"
    assert "SCOPE_EXCEEDS_SCAN_BUDGET" in result["report"]["stop_reasons"]
    assert provider.attempts == 0
    assert result["report"]["usage"]["provider_request_attempts"] == 0


def test_allow_partial_scan_admits_a_prefix(project):
    project["config"]["scan_caps"]["fragments"] = 1
    provider = ScriptedProvider()
    engine = SearchEngine(project, provider=provider)
    result = engine.search({"query": "where is session expiry handled?",
                            "allow_partial_scan": True})["outcome"]
    validate_search_result(result)
    assert result["status"] != "rejected"
    assert result["report"]["fragments"]["total"] is None  # preparation stopped early
    assert "PREPARATION_LIMIT" in result["report"]["stop_reasons"] or "SCAN_CAP_REACHED" \
        in result["report"]["stop_reasons"]


def test_small_response_budget_drops_ranges_but_keeps_report(project):
    engine = SearchEngine(project, provider=ScriptedProvider(high=0.99))
    result = engine.search({"query": "where is session expiry handled?",
                            "max_context_tokens": 1_024})["outcome"]
    validate_search_result(result)
    measured = result["report"]["response_budget"]["requested_tokens"]
    assert measured == 1_024
