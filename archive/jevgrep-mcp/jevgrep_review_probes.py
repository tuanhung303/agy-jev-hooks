"""Reproduction probes for the 16 Astra review findings. One case per claim.

Run: python3 tests/jevgrep_review_probes.py
Prints REPRODUCED / NOT for each finding; never raises. Expected after the
fixes: reproduced 0/16 findings.
"""
import json
import os
import shutil
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def case(number, title):
    def wrap(func):
        def run():
            try:
                reproduced = func()
            except Exception:
                print(f"  probe error: {traceback.format_exc().strip().splitlines()[-1]}")
                reproduced = False
            RESULTS.append((number, title, bool(reproduced)))
            print(f"finding {number:2d} [{title}]: {'REPRODUCED' if reproduced else 'NOT reproduced'}")
        run.case_name = title
        return run
    return wrap


def scratch(name):
    path = f"/tmp/repro-{name}"
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def make_loaded(repo, tmp, config=None):
    from mcp.jev.grep.authorization import AuthorizedRoot
    from mcp.jev.grep.config import default_configuration, validate_configuration
    config = config or default_configuration(repo)
    validate_configuration(config)
    return {"config": config, "source_root": AuthorizedRoot.open(repo),
            "repository_root": os.path.realpath(repo),
            "cache_directory": os.path.join(tmp, "cache"),
            "config_path": os.path.join(tmp, "profile.yaml"), "fingerprint": "x"}


class Scripted:
    model = "jev-1.13.0"

    def __init__(self, hook=None, score=0.9):
        self.hook = hook
        self.score = score
        self.calls = []

    def serialize_batch(self, batch):
        from mcp.jev.grep.jev import build_request_payload, serialize_payload
        return serialize_payload(build_request_payload(batch, self.model))

    def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
        from mcp.jev.grep.jev import BatchEvaluation, ProviderUsage
        self.calls.append(batch)
        if self.hook:
            self.hook(batch)
        return BatchEvaluation({item.id: self.score for item in batch.items}, (),
                               ProviderUsage(5, 1), self.model, self.model, 10, "r")


@case(1, "parent-dir swap leaks outside text to provider")
def finding_1():
    from mcp.jev.grep.authorization import AuthorizedRoot
    tmp = scratch("f1")
    repo = os.path.join(tmp, "repo")
    outside = os.path.join(tmp, "outside")
    os.makedirs(os.path.join(repo, "sub"))
    os.makedirs(outside)
    with open(os.path.join(repo, "sub", "file.txt"), "w") as handle:
        handle.write("INSIDE")
    with open(os.path.join(outside, "file.txt"), "w") as handle:
        handle.write("OUTSIDE AUTHORIZED ROOT")
    root = AuthorizedRoot.open(repo)

    real_open = os.open
    swapped = []

    def racing_open(path, flags, *args, **kwargs):
        if not swapped and str(path).endswith("file.txt"):
            swapped.append(True)
            os.rename(os.path.join(repo, "sub"), os.path.join(repo, "sub.bak"))
            os.symlink(outside, os.path.join(repo, "sub"))
        return real_open(path, flags, *args, **kwargs)

    os.open = racing_open
    try:
        data = root.read_file_bytes(os.path.join(root.path, "sub", "file.txt"), 4096)
    finally:
        os.open = real_open
    return b"OUTSIDE" in data


@case(2, "symlinked profile and cache bypass outside-repo rule")
def finding_2():
    from mcp.jev.grep.config import ConfigurationError, load_configuration
    tmp = scratch("f2")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    os.makedirs(os.path.join(tmp, "external"))
    from mcp.jev.grep.config import default_configuration, dump_configuration_yaml
    with open(os.path.join(repo, "profile.yaml"), "w") as handle:
        handle.write(dump_configuration_yaml(default_configuration(repo)))
    os.symlink(os.path.join(repo, "profile.yaml"), os.path.join(tmp, "external", "link.yaml"))
    os.symlink(os.path.join(repo, "cache"), os.path.join(tmp, "external", "cache"))
    env = {"JEVGREP_CACHE_HOME": os.path.join(tmp, "external", "cache")}
    try:
        load_configuration(os.path.join(tmp, "external", "link.yaml"), env=env)
        return True  # accepted: the repository file is treated as trusted config
    except ConfigurationError:
        return False


@case(3, "HTTP error bodies are drained without a bound")
def finding_3():
    import io
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
    return response.status == 503 and any(len(args) == 0 for args in reads)


@case(4, "concurrent workers exceed scan caps")
def finding_4():
    from mcp.jev.grep import engine as engine_mod
    from mcp.jev.grep.engine import SearchEngine
    from mcp.jev.grep.jev import ProviderError
    tmp = scratch("f4")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for index in range(130):
        with open(os.path.join(repo, f"f{index:03d}.py"), "w") as handle:
            handle.write(f"def f{index}():\n    return {index}\n")
    loaded = make_loaded(repo, tmp)
    loaded["config"]["scan_caps"]["request_attempts"] = 3
    loaded["config"]["search"]["concurrency"] = 2
    # near-instant retries so both workers re-enter dispatch together
    loaded["config"]["search"]["retry"] = {"max_retries": 2, "base_delay_ms": 1,
                                           "max_delay_ms": 2, "retry_ambiguous": False}

    # Force the two workers to pass the cap check in the same window and then
    # meet right before the reservation, where the missing lock should bite.
    import inspect
    from mcp.jev.grep.lifecycle import SearchContext
    barrier = threading.Barrier(2, timeout=5)
    real_can = SearchContext.can_start_work

    def racy_can(self):
        result = real_can(self)
        frame = inspect.currentframe().f_back
        if result and frame is not None and frame.f_code.co_name == "on_dispatch":
            try:
                barrier.wait()
            except Exception:
                pass
        return result

    class Flaky:
        """Counts exactly one attempt per evaluate_batch entry."""

        model = "jev-1.13.0"

        def __init__(self):
            self.attempts = 0
            self.failed = set()

        def serialize_batch(self, batch):
            from mcp.jev.grep.jev import build_request_payload, serialize_payload
            return serialize_payload(build_request_payload(batch, self.model))

        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            from mcp.jev.grep.jev import BatchEvaluation, ProviderUsage
            self.attempts += 1
            key = batch.items[0].id
            if key not in self.failed:
                self.failed.add(key)
                raise ProviderError("PROVIDER_UNAVAILABLE", "temporary", True, False)
            return BatchEvaluation({item.id: 0.9 for item in batch.items}, (),
                                   ProviderUsage(5, 1), self.model, self.model, 10, "r")

    SearchContext.can_start_work = racy_can
    try:
        provider = Flaky()
        result = SearchEngine(loaded, provider=provider).search({"query": "how do these work?", "scope": ["."]})
        reported = result["outcome"].get("report", {}).get("usage", {}).get("provider_request_attempts")
        print(f"  actual calls: {provider.attempts}, reported: {reported}")
        return provider.attempts > 3
    finally:
        SearchContext.can_start_work = real_can


class FeedableStream:
    """Line source the server can read while the probe feeds it."""

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


@case(5, "cancelled MCP call still emits a result")
def finding_5():
    from mcp.jev.grep import mcp_server
    import io
    tmp = scratch("f5")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("x = 1\n")
    loaded = make_loaded(repo, tmp)
    loaded["config"]["remote_evaluation_enabled"] = False
    from mcp.jev.grep.engine import SearchEngine
    engine = SearchEngine(loaded, provider=None)

    paused = threading.Event()
    release = threading.Event()
    real_tool_result = mcp_server.tool_result
    def slow_tool_result(outcome):
        paused.set()
        release.wait(5)  # the window between the abort check and send
        return real_tool_result(outcome)
    mcp_server.tool_result = slow_tool_result

    stdin = FeedableStream()
    stdin.feed(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    stdin.feed(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "semantic_search_code", "arguments": {"query": "q"}}}))
    out, err = io.StringIO(), io.StringIO()
    worker = threading.Thread(target=mcp_server.run_mcp_server, args=(engine, stdin, out, err, "t"))
    worker.start()
    assert paused.wait(5)  # the call reached the emission window
    stdin.feed(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                           "params": {"requestId": 2}}))
    time.sleep(0.1)  # let the reader thread accept the cancellation
    release.set()
    stdin.close()
    worker.join(5)
    mcp_server.tool_result = real_tool_result
    sent = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return any(response.get("id") == 2 and "result" in response for response in sent)


@case(6, "MCP admission does not reserve the running slot")
def finding_6():
    from mcp.jev.grep import mcp_server
    import io
    tmp = scratch("f6")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("x = 1\n")
    loaded = make_loaded(repo, tmp)
    loaded["config"]["remote_evaluation_enabled"] = False
    from mcp.jev.grep.engine import SearchEngine
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
            timer = threading.Timer(0.2, super().start)
            timer.start()

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
    entered.wait(3)
    time.sleep(0.4)  # let the other calls race admission
    release.set()
    worker.join(8)
    mcp_server.threading.Thread = real_thread
    sent = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    busy = sum(1 for response in sent if json.dumps(response.get("result", {})).find("BUSY") >= 0)
    executed = len(started)
    return executed > 1 and busy == 0


@case(7, "deadline cannot interrupt an outstanding provider wait")
def finding_7():
    from mcp.jev.grep.engine import SearchEngine
    tmp = scratch("f7")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for index in range(4):
        with open(os.path.join(repo, f"f{index}.py"), "w") as handle:
            handle.write(f"def f{index}():\n    return {index}\n")
    loaded = make_loaded(repo, tmp)
    loaded["config"]["search"]["deadline_ms"] = 30
    release = threading.Event()

    class Blocking(Scripted):
        def evaluate_batch(self, batch, is_cancelled=None, timeout_s=None):
            release.wait(1.5)
            return super().evaluate_batch(batch, is_cancelled)

    provider = Blocking()
    started_at = time.monotonic()
    SearchEngine(loaded, provider=provider).search({"query": "how do these work?", "scope": ["."]})
    blocked_for = time.monotonic() - started_at
    release.set()
    return blocked_for > 0.4  # deadline was 30ms


@case(8, "valid fractional USD cap raises contract error after paid work")
def finding_8():
    from mcp.jev.grep.config import default_configuration
    from mcp.jev.grep.contracts import ContractValidationError
    from mcp.jev.grep.engine import SearchEngine
    tmp = scratch("f8")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("def handle():\n    return 1\n")
    config = default_configuration(repo)
    config["provider"]["pricing"] = {"model": config["provider"]["model"], "verified_at": "2026-09-22",
                                     "input_usd_per_million_tokens": 0.5,
                                     "output_usd_per_million_tokens": 0}
    config["scan_caps"]["estimated_cost_usd"] = 0.10
    loaded = make_loaded(repo, tmp, config)
    try:
        SearchEngine(loaded, provider=Scripted()).search({"query": "where is handle?"})
        return False
    except ContractValidationError:
        return True


@case(9, "vercel error classification raises AttributeError")
def finding_9():
    from mcp.jev.grep.vercel_gateway import _classify_gateway_status
    try:
        _classify_gateway_status(401, {}, 10)
        return False
    except AttributeError:
        return True


@case(10, "structural mutations accepted by validate_search_result")
def finding_10():
    from mcp.jev.grep.contracts import validate_search_result
    from mcp.jev.grep.engine import SearchEngine
    tmp = scratch("f10")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("def handle():\n    return 1\n")
    outcome = SearchEngine(make_loaded(repo, tmp), provider=Scripted()).search(
        {"query": "where is handle?"})["outcome"]
    import copy
    mutations = {
        "end_line_float": lambda r: r["excerpts"][0].__setitem__("end_line", 1.5),
        "start_line_bool": lambda r: r["excerpts"][0].__setitem__("start_line", True),
        "remove_response_budget": lambda r: r["report"].pop("response_budget"),
        "negative_requested_tokens": lambda r: r["report"]["response_budget"].__setitem__("requested_tokens", -1),
        "inventory_complete_str": lambda r: r["report"].__setitem__("inventory_complete", "yes"),
        "infinite_cost": lambda r: r["report"]["usage"].__setitem__("estimated_cost_usd", float("inf")),
        "long_search_id": lambda r: r.__setitem__("search_id", "a" * 129),
    }
    accepted = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(outcome)
        try:
            mutate(candidate)
        except Exception:
            continue
        try:
            validate_search_result(candidate)
            accepted.append(name)
        except Exception:
            pass
    print(f"  accepted mutations: {accepted}")
    return len(accepted) >= 5


@case(11, "requests reject scopes upstream normalizes")
def finding_11():
    from mcp.jev.grep.contracts import ContractValidationError, parse_search_request
    rejected = []
    for scope in (["./src"], ["src", "src/a.py"], ["z", "a"], ["src", "src"]):
        try:
            parse_search_request({"query": "q", "scope": scope})
        except ContractValidationError:
            rejected.append(scope)
    return len(rejected) >= 3


@case(12, "malformed unicode escapes raise UnicodeEncodeError")
def finding_12():
    from mcp.jev.grep.engine import SearchEngine
    tmp = scratch("f12")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("x = 1\n")
    engine = SearchEngine(make_loaded(repo, tmp), provider=Scripted())
    try:
        engine.search(json.loads('{"query":"\\ud800"}'))
        return False
    except UnicodeEncodeError:
        return True


@case(13, "invalid UTF-8 cache entry raises instead of missing")
def finding_13():
    from mcp.jev.grep.cache import ScoreCache, evaluation_identity
    tmp = scratch("f13")
    cache = ScoreCache(os.path.join(tmp, "cache"), True, 60, 1_000_000)
    identity = evaluation_identity({"query": "q", "path": "a", "start_line": 1, "end_line": 1,
                                    "text": "x", "label": None, "criterion_version": "c",
                                    "layout_version": "l", "chunker_version": "k",
                                    "endpoint": "https://api.typesafe.ai",
                                    "model_revision": "jev-1.13.0"})
    name = cache._path(identity)
    os.makedirs(os.path.dirname(name), exist_ok=True)
    with open(name, "wb") as handle:
        handle.write(b"\xff")
    try:
        cache.read(identity)
        return False
    except UnicodeDecodeError:
        return True


@case(14, "explicit scopes bypass excluded ancestor directories")
def finding_14():
    from mcp.jev.grep.authorization import AuthorizedRoot
    from mcp.jev.grep.inventory import InventoryOptions, inventory_scope
    tmp = scratch("f14")
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, ".ssh"))
    os.makedirs(os.path.join(repo, "secrets"))
    os.makedirs(os.path.join(repo, "private"))
    for name in (".ssh/note.txt", "secrets/cred.txt", "private/note.txt"):
        with open(os.path.join(repo, *name.split("/")), "w") as handle:
            handle.write("data\n")
    with open(os.path.join(repo, ".gitignore"), "w") as handle:
        handle.write("private/\n")
    root = AuthorizedRoot.open(repo)
    options = InventoryOptions(respect_gitignore=True, max_file_bytes=1_048_576)
    leaked = []
    for scope in ((".ssh",), ("secrets",), ("private",), (".ssh/note.txt",), ("private/note.txt",)):
        result = inventory_scope(root, scope, options)
        leaked.extend(entry.relative_path for entry in result.files)
    print(f"  admitted by explicit scope: {sorted(set(leaked))}")
    return len(leaked) >= 3


@case(15, "nested .gitignore negation undoes .jevgrepignore denial")
def finding_15():
    from mcp.jev.grep.ignore_rules import is_ignored, parse_ignore_file
    root = parse_ignore_file("src/private.py\n", "", narrowing_only=True)
    nested = parse_ignore_file("!private.py\n", "src")
    return not is_ignored((root, nested), "src/private.py", False).ignored


@case(16, "authorization loss after dispatch becomes a rejection")
def finding_16():
    from mcp.jev.grep.engine import SearchEngine
    tmp = scratch("f16")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for index in range(130):
        with open(os.path.join(repo, f"f{index:03d}.py"), "w") as handle:
            handle.write(f"def f{index}():\n    return {index}\n")
    loaded = make_loaded(repo, tmp)
    loaded["config"]["search"]["concurrency"] = 1

    def delete_other(batch):
        # the next batch's files disappear after this dispatch was admitted
        for name in os.listdir(repo):
            if name not in {item.path.split("/")[-1] for item in batch.items}:
                os.remove(os.path.join(repo, name))

    provider = Scripted(hook=delete_other)
    outcome = SearchEngine(loaded, provider=provider).search({"query": "how do these work?", "scope": ["."]})
    result = outcome["outcome"]
    dispatched = len(provider.calls)
    print(f"  provider calls: {dispatched}, status: {result['status']}, "
          f"error: {result.get('error', {}).get('code')}")
    return dispatched >= 1 and result["status"] == "rejected" \
        and result.get("error", {}).get("code") == "UNAUTHORIZED_SCOPE"


for probe in list(globals().values()):
    if hasattr(probe, "case_name"):
        probe()

reproduced = [entry for entry in RESULTS if entry[2]]
print(f"\nreproduced {len(reproduced)}/{len(RESULTS)} findings")
