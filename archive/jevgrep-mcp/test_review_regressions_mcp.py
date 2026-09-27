"""Archived 2026-09-28 with the jevgrep MCP server: regression tests 5 and 6 from
tests/test_jevgrep_review_regressions.py, which drive mcp_server.run_mcp_server.
They reuse that file's Scripted and make_loaded helpers and are not collected."""


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
