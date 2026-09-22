"""Offline tests for the JevGrep port evaluation layer: cache, batching, adapter
validation and the scheduler with a scripted provider."""
import json

import pytest

from mcp.jev.grep.cache import ScoreCache, evaluation_identity, is_cache_entry
from mcp.jev.grep.jev import (BatchEvaluation, BatchItem, EvaluationBatch, JevAdapter, ProviderError,
                              ProviderUsage, build_request_payload, inspect_response_keys,
                              normalize_response, serialize_payload)
from mcp.jev.grep.policy import batch_limits, fits_serialized_batch, score_cache_policy
from mcp.jev.grep.scheduler import run_evaluations
from mcp.jev.grep.lifecycle import SearchContext


def _batch(items=2) -> EvaluationBatch:
    return EvaluationBatch("where is expiry handled?", tuple(
        BatchItem(f"src/a.py#L{i}-L{i + 1}", "src/a.py", i, i + 1, f"line {i}\nline {i + 1}\n")
        for i in range(1, items + 1)))


def test_evaluation_identity_binds_every_visible_input():
    base = {
        "query": "q", "path": "a.py", "start_line": 1, "end_line": 2, "text": "x = 1\n",
        "label": None, "criterion_version": "criterion-1", "layout_version": "layout-a-1",
        "chunker_version": "jevgrep-py-line-windows-1", "endpoint": "https://api.typesafe.ai/",
        "model_revision": "jev-1.13.0", "provider_options": {"adapter": "typesafe-direct"},
        "batch_composition": ["hash"],
    }
    first = evaluation_identity(base)
    assert first == evaluation_identity(dict(base))
    assert first == evaluation_identity(dict(base, endpoint="https://api.typesafe.ai"))
    assert first != evaluation_identity(dict(base, text="x = 2\n"))
    assert first != evaluation_identity(dict(base, model_revision="jev-1.13.1"))
    assert first != evaluation_identity(dict(base, query="other"))


def test_cache_ttl_expiry_and_corruption(tmp_path):
    now = [1_000]
    cache = ScoreCache(str(tmp_path), enabled=True, ttl_seconds=10, max_bytes=1_000_000,
                       rolling_ttl_seconds=5, now=lambda: now[0])
    identity = evaluation_identity({
        "query": "q", "path": "a.py", "start_line": 1, "end_line": 1, "text": "x",
        "label": None, "criterion_version": "c", "layout_version": "l", "chunker_version": "k",
        "endpoint": "https://api.typesafe.ai", "model_revision": "jev-1.13.0",
    })
    assert cache.read(identity) is None
    assert cache.write(identity, 0.75, {"model_revision": "jev-1.13.0", "layout": "l",
                                        "criterion": "c", "chunker": "k"})
    assert cache.read(identity) == 0.75
    now[0] = 1_000 + 11 * 1000  # past the TTL: today's policy bites existing entries
    assert cache.read(identity) is None

    assert cache.write(identity, 0.5, {"model_revision": "jev-1.13.0", "layout": "l",
                                       "criterion": "c", "chunker": "k"})
    name = cache._path(identity)
    with open(name, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    now[0] = 1_000
    assert cache.read(identity) is None
    assert cache.stats.corrupt == 1


def test_score_cache_policy_modes():
    pinned = score_cache_policy("typesafe-direct", "jev-1.13.0",
                                {"enabled": True, "ttl_seconds": 100, "rolling_ttl_seconds": 5})
    assert pinned == {"mode": "pinned", "ttl_seconds": 100}
    rolling = score_cache_policy("vercel-ai-gateway", "typesafe-ai/jev",
                                 {"enabled": True, "ttl_seconds": 100, "rolling_ttl_seconds": 5})
    assert rolling == {"mode": "rolling", "ttl_seconds": 5}
    assert score_cache_policy("typesafe-direct", "jev-latest",
                              {"enabled": True, "ttl_seconds": 100})["mode"] == "rolling"
    assert score_cache_policy("typesafe-direct", "jev-1.13.0",
                              {"enabled": False, "ttl_seconds": 100})["mode"] == "disabled"


def test_batching_respects_serialized_limits():
    from mcp.jev.grep.engine import build_batches
    from mcp.jev.grep.chunker import PreparedFragment

    fragments = [PreparedFragment(
        id=f"src/a.py#L{index}-L{index}", path="src/a.py", sha256="0" * 64,
        start_line=index, end_line=index, byte_start=0, byte_end=1, text="x = 1\n",
        byte_count=6, token_count=3, chunker="jevgrep-py-line-windows-1")
        for index in range(1, 120)]
    batches = build_batches(fragments, "q", {"model": "jev-1.13.0", "limits": batch_limits()})
    assert len(batches) > 1
    assert sum(len(batch.items) for batch in batches) == len(fragments)
    for batch in batches:
        body = serialize_payload(build_request_payload(batch, "jev-1.13.0"))
        assert fits_serialized_batch(body, batch_limits())
        assert len(batch.items) <= batch_limits()["max_items"]
    # deterministic: same fragments in, same batches out
    assert [tuple(item.id for item in batch.items) for batch in batches] == [
        tuple(item.id for item in batch.items) for batch in
        build_batches(list(reversed(fragments)), "q", {"model": "jev-1.13.0", "limits": batch_limits()})]


def test_normalize_response_keeps_valid_neighbours():
    batch = _batch()
    body = {
        "answers": {
            "src/a.py#L1-L2": {"type": "noul", "noul": 0.9},
            "src/a.py#L2-L3": {"type": "noul", "noul": 2.0},  # out of range
        },
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "model": "jev-1.13.0",
    }
    evaluation = normalize_response(body, batch, "jev-1.13.0", 100, "req-1")
    assert evaluation.scores == {"src/a.py#L1-L2": 0.9}
    assert [(item.id, item.reason) for item in evaluation.invalid] == [("src/a.py#L2-L3", "out_of_range")]
    assert evaluation.usage == ProviderUsage(10, 2)

    # a missing usage is unknown, never zero
    unknown = normalize_response({"answers": body["answers"]}, batch, "jev-1.13.0", 100, None)
    assert unknown.usage.input_tokens is None

    with pytest.raises(ProviderError):
        normalize_response({"no_answers": True}, batch, "jev-1.13.0", 100, None)


def test_duplicate_response_keys_invalidate_answers():
    # Raw text on purpose: json.loads keeps the last duplicate key silently.
    raw = ('{"answers": {"a": {"type": "noul", "noul": 0.5}, "a": {"type": "noul", "noul": 0.5}}, '
           '"usage": {"input_tokens": 1}, "usage": {"input_tokens": 1}}')
    duplicates, usage_ambiguous = inspect_response_keys(raw, ("a",))
    assert "a" in duplicates
    assert usage_ambiguous


def test_classify_status_families():
    from mcp.jev.grep.jev import classify_status
    assert classify_status(401, {}, 10).code == "PROVIDER_AUTH"
    assert classify_status(429, {"retry-after": "1"}, 10).retry_after_ms == 1000
    assert classify_status(503, {}, 10).ambiguous
    assert classify_status(302, {}, 10).code == "PROVIDER_UNAVAILABLE"
    assert not classify_status(302, {}, 10).retryable


class _ScriptedTransport:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = body or {}
        self.headers = headers or {}
        self.requests = []

    def __call__(self, url, headers, body, timeout=None, is_cancelled=None):
        from mcp.jev.grep.http import TransportResponse
        self.requests.append((url, headers, body))
        return TransportResponse(self.status, self.headers, json.dumps(self.body))


def test_jev_adapter_one_attempt_and_no_redirect_following():
    batch = _batch()
    transport = _ScriptedTransport(body={
        "answers": {item.id: {"type": "noul", "noul": 0.8} for item in batch.items},
        "usage": {"input_tokens": 12, "output_tokens": 1},
    })
    adapter = JevAdapter("https://api.typesafe.ai", "jev-1.13.0", "key", transport=transport)
    evaluation = adapter.evaluate_batch(batch)
    assert evaluation.scores and len(transport.requests) == 1
    assert transport.requests[0][0] == "https://api.typesafe.ai/v1/systemone"
    assert transport.requests[0][1]["authorization"] == "Bearer key"

    redirect = _ScriptedTransport(status=302)
    adapter = JevAdapter("https://api.typesafe.ai", "jev-1.13.0", "key", transport=redirect)
    with pytest.raises(ProviderError) as failure:
        adapter.evaluate_batch(batch)
    assert failure.value.code == "PROVIDER_UNAVAILABLE" and not failure.value.retryable


def test_scheduler_retries_transient_and_stops_on_auth():
    from mcp.jev.grep.jev import ProviderError as PE

    class Flaky:
        model = "jev-1.13.0"

        def __init__(self):
            self.calls = 0

        def serialize_batch(self, batch):
            return serialize_payload(build_request_payload(batch, self.model))

        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            self.calls += 1
            if self.calls == 1:
                raise PE("PROVIDER_UNAVAILABLE", "temporary", True, False)
            return BatchEvaluation({item.id: 0.5 for item in batch.items}, (),
                                   ProviderUsage(3, 1), self.model, self.model, 10, "r")

    class Dead:
        model = "jev-1.13.0"

        def serialize_batch(self, batch):
            return "{}"

        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            raise PE("PROVIDER_AUTH", "bad key", False, False)

    batches = [_batch(1)]
    context = SearchContext(deadline_ms=10_000)
    outcomes = {"scores": [], "failures": []}
    flaky = Flaky()
    run_evaluations(flaky, batches, context, {
        "concurrency": 1, "random": lambda: 0.5,
        "on_scores": lambda batch, evaluation: outcomes["scores"].append(evaluation),
        "on_failure": lambda batch, failure, retry: outcomes["failures"].append((failure.code, retry)),
    })
    assert flaky.calls == 2 and len(outcomes["scores"]) == 1
    assert outcomes["failures"] == [("PROVIDER_UNAVAILABLE", True)]

    context = SearchContext(deadline_ms=10_000)
    failures = []
    run_evaluations(Dead(), batches, context, {
        "concurrency": 1, "random": lambda: 0.5,
        "on_scores": lambda batch, evaluation: None,
        "on_failure": lambda batch, failure, retry: failures.append((failure.code, retry)),
    })
    assert failures == [("PROVIDER_AUTH", False)]
    assert context.stop_reasons() == []  # reasons belong to the engine's handlers
