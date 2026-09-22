"""Regression tests pinned from the 16 reproduced Astra review findings.

Each test asserts the corrected behavior; the pre-fix reproductions live in
tests/jevgrep_review_probes.py and must come back 0/16 after these pass.
"""
import copy
import inspect
import io
import json
import os
import shutil
import threading
import time

import pytest

from mcp.jev.grep.authorization import AuthorizedRoot, UnauthorizedPathError
from mcp.jev.grep.cache import ScoreCache, evaluation_identity
from mcp.jev.grep.config import ConfigurationError, default_configuration, dump_configuration_yaml, load_configuration
from mcp.jev.grep.contracts import ContractValidationError, parse_search_request, validate_search_result
from mcp.jev.grep.engine import SearchEngine
from mcp.jev.grep.ignore_rules import is_ignored, parse_ignore_file
from mcp.jev.grep.inventory import InventoryOptions, inventory_scope
from mcp.jev.grep.jev import (BatchEvaluation, ProviderError, ProviderUsage, build_request_payload,
                              serialize_payload)


class Scripted:
    model = "jev-1.13.0"

    def __init__(self, score=0.9):
        self.score = score
        self.attempts = 0

    def serialize_batch(self, batch):
        return serialize_payload(build_request_payload(batch, self.model))

    def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
        self.attempts += 1
        return self.respond(batch)

    def respond(self, batch):
        return BatchEvaluation({item.id: self.score for item in batch.items}, (),
                               ProviderUsage(5, 1), self.model, self.model, 10, "r")


def make_loaded(repo, tmp, config=None):
    config = config or default_configuration(repo)
    return {"config": config, "source_root": AuthorizedRoot.open(repo),
            "repository_root": os.path.realpath(repo),
            "cache_directory": os.path.join(str(tmp), "cache"),
            "config_path": os.path.join(str(tmp), "profile.yaml"), "fingerprint": "x"}


def make_repo(tmp, files):
    repo = os.path.join(str(tmp), "repo")
    os.makedirs(repo, exist_ok=True)
    for name, body in files.items():
        path = os.path.join(repo, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    return repo


# 1: a parent-directory swap must refuse the read, never leak outside text
def test_1_parent_dir_swap_is_refused(tmp_path):
    repo = make_repo(tmp_path, {"sub/file.txt": "INSIDE"})
    outside = os.path.join(str(tmp_path), "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "file.txt"), "w", encoding="utf-8") as handle:
        handle.write("OUTSIDE AUTHORIZED ROOT")
    root = AuthorizedRoot.open(repo)

    real_open = os.open
    swapped = []

    def racing_open(path, flags, *args, **kwargs):
        if not swapped and str(path).endswith("file.txt"):
            swapped.append(True)
            os.rename(os.path.join(root.path, "sub"), os.path.join(root.path, "sub.bak"))
            os.symlink(outside, os.path.join(root.path, "sub"))
        return real_open(path, flags, *args, **kwargs)

    os.open = racing_open
    try:
        with pytest.raises(UnauthorizedPathError):
            root.read_file_bytes(os.path.join(root.path, "sub", "file.txt"), 4096)
    finally:
        os.open = real_open
        os.unlink(os.path.join(root.path, "sub"))
        os.rename(os.path.join(root.path, "sub.bak"), os.path.join(root.path, "sub"))


# 2: symlinked profile and cache must fail containment after canonicalization
def test_2_symlinked_profile_and_cache_rejected(tmp_path):
    repo = make_repo(tmp_path, {"a.py": "x = 1\n"})
    external = os.path.join(str(tmp_path), "external")
    os.makedirs(external)
    profile = os.path.join(repo, "profile.yaml")
    with open(profile, "w", encoding="utf-8") as handle:
        handle.write(dump_configuration_yaml(default_configuration(repo)))
    os.symlink(profile, os.path.join(external, "link.yaml"))
    os.symlink(os.path.join(repo, "cache"), os.path.join(external, "cache"))
    with pytest.raises(ConfigurationError):
        load_configuration(os.path.join(external, "link.yaml"),
                           env={"JEVGREP_CACHE_HOME": os.path.join(external, "cache")})


# 3: error responses are never drained without a bound
def test_3_error_bodies_never_drained():
    from unittest import mock
    from mcp.jev.grep.http import bounded_request
    reads = []

    class FakeRaw:
        status = 503
        def getheaders(self):
            return []
        def read(self, *args):
            reads.append(args)
            return b"x"

    class FakeConn:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return FakeRaw()
        def close(self):
            pass

    with mock.patch("http.client.HTTPSConnection", FakeConn):
        response = bounded_request("https://api.typesafe.ai/v1/systemone", {}, "{}", 5.0)
    assert response.status == 503
    assert reads == []  # the error body is never touched


# 4: concurrent dispatch waves cannot exceed the attempt cap
def test_4_concurrent_dispatch_respects_caps(tmp_path):
    from mcp.jev.grep import engine as engine_mod
    repo = make_repo(tmp_path, {f"f{index:03d}.py": f"def f{index}():\n    return {index}\n"
                                for index in range(130)})
    loaded = make_loaded(repo, tmp_path)
    loaded["config"]["scan_caps"]["request_attempts"] = 3
    loaded["config"]["search"]["concurrency"] = 2
    loaded["config"]["search"]["retry"] = {"max_retries": 2, "base_delay_ms": 1,
                                           "max_delay_ms": 2, "retry_ambiguous": False}

    # worker waves meet just before the ledger transaction
    barrier = threading.Barrier(2, timeout=1)
    real_estimate = engine_mod.estimate_batch_tokens

    def rendezvous_estimate(batch, model, serialize):
        frame = inspect.currentframe().f_back
        if frame is not None and frame.f_code.co_name == "on_dispatch":
            try:
                barrier.wait()
            except Exception:
                pass
        return real_estimate(batch, model, serialize)

    class Flaky(Scripted):
        def __init__(self):
            super().__init__()
            self.failed = set()

        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            self.attempts += 1
            key = batch.items[0].id
            if key not in self.failed:
                self.failed.add(key)
                raise ProviderError("PROVIDER_UNAVAILABLE", "temporary", True, False)
            return self.respond(batch)

    engine_mod.estimate_batch_tokens = rendezvous_estimate
    try:
        provider = Flaky()
        outcome = SearchEngine(loaded, provider=provider).search(
            {"query": "how do these work?", "scope": ["."]})["outcome"]
    finally:
        engine_mod.estimate_batch_tokens = real_estimate
    assert provider.attempts <= 3
    assert outcome["report"]["usage"]["provider_request_attempts"] == provider.attempts


class FeedableStream:
    def __init__(self):
        self.queue = []
        self.event = threading.Event()
        self.done = False

    def feed(self, line):
        self.queue.append(line)
        self.event.set()

    def close(self):
        self.done = True
        self.event.set()

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            self.event.clear()
            if self.queue:
                return self.queue.pop(0)
            if self.done:
                raise StopIteration
            self.event.wait(1.0)


# 5: a cancelled call never emits a later result
def test_5_cancelled_call_never_emits(tmp_path):
    from mcp.jev.grep import mcp_server
    repo = make_repo(tmp_path, {"a.py": "x = 1\n"})
    loaded = make_loaded(repo, tmp_path)
    loaded["config"]["remote_evaluation_enabled"] = False
    engine = SearchEngine(loaded, provider=None)

    paused = threading.Event()
    release = threading.Event()
    real_tool_result = mcp_server.tool_result

    def slow_tool_result(outcome):
        paused.set()
        release.wait(5)  # the window between the abort check and the write
        return real_tool_result(outcome)

    mcp_server.tool_result = slow_tool_result
    stdin = FeedableStream()
    stdin.feed(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    stdin.feed(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "semantic_search_code", "arguments": {"query": "q"}}}))
    out, err = io.StringIO(), io.StringIO()
    worker = threading.Thread(target=mcp_server.run_mcp_server, args=(engine, stdin, out, err, "t"))
    worker.start()
    assert paused.wait(5)
    stdin.feed(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                           "params": {"requestId": 2}}))
    time.sleep(0.1)
    release.set()
    stdin.close()
    worker.join(5)
    mcp_server.tool_result = real_tool_result
    sent = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert not any(response.get("id") == 2 and "result" in response for response in sent)


# 6: admission reserves the running slot before any thread starts
def test_6_admission_reserves_running_slot(tmp_path):
    from mcp.jev.grep import mcp_server
    repo = make_repo(tmp_path, {"a.py": "x = 1\n"})
    loaded = make_loaded(repo, tmp_path)
    loaded["config"]["remote_evaluation_enabled"] = False
    engine = SearchEngine(loaded, provider=None)

    entered = threading.Event()
    release = threading.Event()
    started = []
    real_search = engine.search

    def slow_search(*args, **kwargs):
        started.append(1)
        entered.set()
        release.wait(5)
        return real_search(*args, **kwargs)

    engine.search = slow_search
    real_thread = mcp_server.threading.Thread

    class DelayedThread(real_thread):
        def start(self):
            threading.Timer(0.2, super().start).start()

    mcp_server.threading.Thread = DelayedThread
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})]
    for index in (2, 3, 4):
        lines.append(json.dumps({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                                 "params": {"name": "semantic_search_code",
                                            "arguments": {"query": f"q{index}"}}}))
    stdin = io.StringIO("\n".join(lines) + "\n")
    out, err = io.StringIO(), io.StringIO()
    worker = threading.Thread(target=mcp_server.run_mcp_server, args=(engine, stdin, out, err, "t"))
    worker.start()
    assert entered.wait(5)
    time.sleep(0.4)
    assert len(started) == 1  # the slot was reserved before any thread ran
    release.set()
    worker.join(8)
    mcp_server.threading.Thread = real_thread
    sent = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    busy = sum(1 for response in sent if "BUSY" in json.dumps(response.get("result", {})))
    assert busy == 1          # the third request is refused
    assert len(started) == 2  # the queued request runs only after the first finishes


# 7: the deadline stops the wait even when a provider blocks
def test_7_deadline_stops_waiting_on_blocked_provider(tmp_path):
    repo = make_repo(tmp_path, {f"f{index}.py": f"def f{index}():\n    return {index}\n"
                                for index in range(4)})
    loaded = make_loaded(repo, tmp_path)
    loaded["config"]["search"]["deadline_ms"] = 30
    release = threading.Event()

    class Blocking(Scripted):
        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            release.wait(5)
            return super().evaluate_batch(batch, is_cancelled, timeout_s)

    provider = Blocking()
    started_at = time.monotonic()
    SearchEngine(loaded, provider=provider).search({"query": "how do these work?", "scope": ["."]})
    elapsed = time.monotonic() - started_at
    release.set()
    assert elapsed < 1.0  # the 30ms deadline, not the blocked provider


# 8: fractional USD caps stay valid through validation
def test_8_fractional_usd_cap_validated(tmp_path):
    repo = make_repo(tmp_path, {"a.py": "def handle():\n    return 1\n"})
    config = default_configuration(repo)
    config["provider"]["pricing"] = {"model": config["provider"]["model"], "verified_at": "2026-09-22",
                                     "input_usd_per_million_tokens": 0.5,
                                     "output_usd_per_million_tokens": 0}
    config["scan_caps"]["estimated_cost_usd"] = 0.10
    outcome = SearchEngine(make_loaded(repo, tmp_path, config),
                           provider=Scripted()).search({"query": "where is handle?"})["outcome"]
    validate_search_result(outcome)
    assert outcome["report"]["usage"]["estimated_cost_usd"] < 0.10


# 9: gateway status classification returns normalized errors, never AttributeError
def test_9_gateway_status_classification():
    from mcp.jev.grep.vercel_gateway import _classify_gateway_status
    for status in (401, 402, 403, 409, 429, 503):
        failure = _classify_gateway_status(status, {}, 10)
        assert isinstance(failure, ProviderError)
        assert failure.status == status


# 10: structural mutations are rejected before relational checks
def test_10_structural_mutations_rejected(tmp_path):
    repo = make_repo(tmp_path, {"a.py": "def handle():\n    return 1\n"})
    outcome = SearchEngine(make_loaded(repo, tmp_path), provider=Scripted()).search(
        {"query": "where is handle?"})["outcome"]
    mutations = {
        "end_line_float": lambda r: r["excerpts"][0].__setitem__("end_line", 1.5),
        "start_line_bool": lambda r: r["excerpts"][0].__setitem__("start_line", True),
        "remove_response_budget": lambda r: r["report"].pop("response_budget"),
        "negative_requested_tokens": lambda r: r["report"]["response_budget"].__setitem__("requested_tokens", -1),
        "inventory_complete_str": lambda r: r["report"].__setitem__("inventory_complete", "yes"),
        "infinite_cost": lambda r: r["report"]["usage"].__setitem__("estimated_cost_usd", float("inf")),
        "long_search_id": lambda r: r.__setitem__("search_id", "a" * 129),
    }
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(outcome)
        mutate(candidate)
        with pytest.raises(ContractValidationError):
            validate_search_result(candidate)


# 11: caller scope spelling is normalized, never rejected
def test_11_scope_normalization():
    assert parse_search_request({"query": "q", "scope": ["./src"]})["scope"] == ["src"]
    assert parse_search_request({"query": "q", "scope": ["src", "src/a.py"]})["scope"] == ["src"]
    assert parse_search_request({"query": "q", "scope": ["z", "a"]})["scope"] == ["a", "z"]
    assert parse_search_request({"query": "q", "scope": ["src", "src"]})["scope"] == ["src"]


# 12: malformed unicode becomes a bounded rejection
def test_12_malformed_unicode_is_bounded(tmp_path):
    repo = make_repo(tmp_path, {"a.py": "x = 1\n"})
    engine = SearchEngine(make_loaded(repo, tmp_path), provider=Scripted())
    outcome = engine.search(json.loads('{"query":"\\ud800"}'))["outcome"]
    assert outcome["status"] == "rejected"
    assert outcome["error"]["code"] == "INVALID_REQUEST"


# 13: a broken cache entry is a miss, not an exception
def test_13_cache_decode_error_is_miss(tmp_path):
    cache = ScoreCache(os.path.join(str(tmp_path), "cache"), True, 60, 1_000_000)
    identity = evaluation_identity({"query": "q", "path": "a", "start_line": 1, "end_line": 1,
                                    "text": "x", "label": None, "criterion_version": "c",
                                    "layout_version": "l", "chunker_version": "k",
                                    "endpoint": "https://api.typesafe.ai",
                                    "model_revision": "jev-1.13.0"})
    name = cache._path(identity)
    os.makedirs(os.path.dirname(name), exist_ok=True)
    with open(name, "wb") as handle:
        handle.write(b"\xff")
    assert cache.read(identity) is None
    assert cache.stats.failures == 1


# 14: explicit scopes run the full ancestor exclusion chain
def test_14_explicit_scope_honors_exclusions(tmp_path):
    repo = make_repo(tmp_path, {
        ".ssh/note.txt": "data\n", "secrets/cred.txt": "data\n",
        "private/note.txt": "data\n", ".gitignore": "private/\n",
    })
    root = AuthorizedRoot.open(repo)
    options = InventoryOptions(respect_gitignore=True, max_file_bytes=1_048_576)
    for scope in ((".ssh",), ("secrets",), ("private",), (".ssh/note.txt",), ("private/note.txt",)):
        result = inventory_scope(root, scope, options)
        assert result.files == []


# 15: a narrowing denial is sticky against later negations
def test_15_narrowing_veto_is_sticky():
    root = parse_ignore_file("src/private.py\n", "", narrowing_only=True)
    nested = parse_ignore_file("!private.py\n", "src")
    decision = is_ignored((root, nested), "src/private.py", False)
    assert decision.ignored and decision.narrowing


# 16: authorization loss after dispatch keeps paid work as a partial result
def test_16_post_dispatch_loss_stays_partial(tmp_path):
    repo = make_repo(tmp_path, {f"f{index:03d}.py": f"def f{index}():\n    return {index}\n"
                                for index in range(130)})
    loaded = make_loaded(repo, tmp_path)
    loaded["config"]["search"]["concurrency"] = 1

    class Deleting(Scripted):
        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            result = super().evaluate_batch(batch, is_cancelled, timeout_s)
            keep = {item.path.split("/")[-1] for item in batch.items}
            for name in os.listdir(repo):
                if name not in keep:
                    os.remove(os.path.join(repo, name))
            return result

    provider = Deleting()
    outcome = SearchEngine(loaded, provider=provider).search(
        {"query": "how do these work?", "scope": ["."]})["outcome"]
    assert provider.attempts >= 1
    assert outcome["status"] != "rejected"
    assert "SOURCE_CHANGED" in outcome["report"]["stop_reasons"]
    assert outcome["report"]["files"]["changed_before_return"] > 0
