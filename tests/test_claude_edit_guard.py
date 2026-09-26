import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sage.jev import transport

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("claude_edit_guard", REPO / "hooks/claude-edit-guard.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def payload(tool="Edit", path="tests/test_demo.py", old="assert result == 4", new="assert result == 4", line=11):
    lines = ([f"-{old}", f"+{new}"] if old else [f"+{new}"])
    return {
        "session_id": "sess", "tool_use_id": "tool-1", "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/tmp/project", "tool_name": tool,
        "tool_input": {"file_path": path, "old_string": old, "new_string": new},
        "tool_response": {"filePath": path, "userModified": False,
                          "structuredPatch": [{"oldStart": line, "oldLines": 1, "newStart": line,
                                               "newLines": 1, "lines": lines}]},
    }


class EditGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_state = guard.STATE_DIR
        guard.STATE_DIR = Path(self.temp.name)

    def tearDown(self):
        guard.STATE_DIR = self.old_state
        self.temp.cleanup()

    def analyze(self, p):
        with mock.patch.object(guard, "_call_jev", return_value={"answers": {"q_tamper": {"type": "boolean", "probability": 0.1},
                "q_shallow": {"type": "boolean", "probability": 0.1}, "q_slop": {"type": "boolean", "probability": 0.1}}}):
            return guard.analyze(p)

    def test_edit_deleted_assertion_has_line_number(self):
        p = payload(old="    assert service.run() == 'ok'", new="    service.run()", line=22)
        rec, _ = self.analyze(p)
        self.assertEqual(rec["signals"]["tamper"], [22])

    def test_write_create_uses_added_content(self):
        p = payload("Write", "tests/test_new.py")
        p["tool_input"] = {"file_path": "tests/test_new.py", "content": "def test_empty():\n    pass"}
        p["tool_response"] = {"filePath": "tests/test_new.py", "type": "create", "originalFile": None,
                              "content": "def test_empty():\n    pass", "structuredPatch": [], "userModified": False}
        rec, _ = self.analyze(p)
        self.assertEqual(rec["signals"]["shallow"], [1])

    def test_write_update_reads_structured_patch(self):
        p = payload("Write", "tests/test_demo.py", "assert status == 200", "assert status == 200")
        p["tool_response"].update(type="update", originalFile="assert status == 200")
        rec, _ = self.analyze(p)
        self.assertIn("tamper", rec["signals"])

    def test_bare_mock_assertion_is_shallow_candidate(self):
        signals = guard._signals("test", [[("new", 4, "service.run.assert_called_once()")]])
        self.assertIn("shallow", signals)

    def test_removed_test_invocation_and_snapshot_are_tamper_candidates(self):
        invocation = guard._signals("test", [[("old", 8, "pytest.main(['tests'])")]])
        snapshot = guard._signals("test", [[("new", 3, "- Expected: 4")]], "tests/__snapshots__/view.snap")
        self.assertIn("tamper", invocation)
        self.assertIn("tamper", snapshot)

    def test_multiedit_fallback(self):
        p = payload("MultiEdit")
        p["tool_response"].pop("structuredPatch")
        p["tool_input"] = {"file_path": "tests/test_x.py", "edits": [{"old_string": "assert f()", "new_string": "f()"}]}
        rec, _ = self.analyze(p)
        self.assertIn("tamper", rec["signals"])

    def test_missing_malformed_patch_and_user_modified_skip(self):
        for change in ({"structuredPatch": []}, {"structuredPatch": [{"lines": "bad"}]}, {"userModified": True}):
            p = payload()
            p["tool_response"].update(change)
            if "userModified" in change:
                p["tool_response"]["userModified"] = True
            self.assertIsNone(self.analyze(p)[0])

    def test_non_code_skipped(self):
        self.assertIsNone(self.analyze(payload(path="notes.txt"))[0])

    def test_large_write_sends_bounded_context_containing_signal(self):
        content = "\n".join([f"    value_{i} = {i}" for i in range(299)] +
                             ["def test_final():", "    assert True"])
        p = payload("Write", "tests/test_large.py")
        p["tool_input"] = {"file_path": "tests/test_large.py", "content": content}
        p["tool_response"] = {"filePath": "tests/test_large.py", "type": "create", "originalFile": None,
                              "content": content, "structuredPatch": [], "userModified": False}
        def jev(state, questions, *_):
            self.assertLess(len(state["hunk"]), 8000)
            self.assertIn("new:301:     assert True", state["hunk"])
            return {"answers": {"q_shallow": {"type": "boolean", "probability": .1}}}
        with mock.patch.object(guard, "_call_jev", side_effect=jev):
            record, _ = guard.analyze(p)
        self.assertIsNone(record["error"])

    def test_large_write_signals_are_linear_time(self):
        hunk = [("new", i, f"    value_{i} = {i}") for i in range(1, 3000)]
        started = time.monotonic()
        guard._signals("source", [hunk])
        self.assertLess(time.monotonic() - started, .5)

    def test_swallow_marks_only_matching_added_lines(self):
        lines = [("new", i, f"value_{i} = {i}") for i in range(1, 21)]
        lines += [("new", 21, "except OSError:"), ("new", 22, "    pass")]
        lines += [("new", i, f"value_{i} = {i}") for i in range(23, 41)]
        self.assertEqual(guard._signals("source", [lines])["slop"], [21, 22])

    def test_prefilters_positive_and_near_misses(self):
        positives = [
            ("test", "assert x == 1", "x == 1", "tamper"),
            ("test", "", "assert True", "shallow"),
            ("source", "", "except ValueError:\n    pass", "slop"),
        ]
        for kind, old, new, category in positives:
            hunk = ([('old', 1, old)] if old else []) + [('new', 2, n) for n in new.splitlines()]
            signals = guard._signals(kind, [hunk])
            self.assertIn(category, signals)
        self.assertIn("slop", guard._signals("source", [[("new", 3, "return None")]]))
        near = [
            ("test", [("new", 4, "assert response.matches_schema()")]),
            ("test", [("new", 4, "    assert result.value == expected")]),
            ("source", [("new", 4, "except OSError as exc: log.warning('%s', exc)")]),
        ]
        for kind, hunk in near:
            self.assertFalse(guard._signals(kind, [hunk]))

    def test_dedupe_repeated_event(self):
        p = payload(old="assert f()", new="f()")
        self.analyze(p)
        self.assertIsNone(self.analyze(p)[0])

    def test_call_cap(self):
        guard._bump("sess", "calls-tamper")
        for _ in range(9): guard._bump("sess", "calls-tamper")
        p = payload(old="assert f()", new="f()")
        with mock.patch.object(guard, "_call_jev") as jev:
            rec, _ = guard.analyze(p)
            self.assertEqual(rec["error"], "call cap")
            jev.assert_not_called()

    def test_category_caps_do_not_spend_another_category_budget(self):
        guard._bump("sess", "calls-shallow")
        for _ in range(9):
            guard._bump("sess", "calls-shallow")
        p = payload(old="assert result == 1", new="assert isinstance(result, Result)\nassert result.value == 1")
        with mock.patch.object(guard, "_call_jev", return_value={"answers": {
                "q_tamper": {"type": "boolean", "probability": .1}}}) as jev:
            rec, _ = guard.analyze(p)
        self.assertEqual(rec["error"], None)
        self.assertEqual(guard._count("sess", "calls-tamper"), 1)
        jev.assert_called_once()
        self.assertEqual(set(jev.call_args.args[1]), {"q_tamper"})

    def test_shallow_and_slop_calls_do_not_consume_tamper_fuse(self):
        shallow = payload(old="", new="assert True")
        shallow["tool_use_id"] = "shallow-only"
        with mock.patch.object(guard, "_call_jev", return_value={"answers": {
                "q_shallow": {"type": "boolean", "probability": .1}}}):
            self.assertIsNotNone(guard.analyze(shallow)[0])
        self.assertEqual(guard._count("sess", "calls-tamper"), 0)
        tamper = payload(old="assert f()", new="f()")
        tamper["tool_use_id"] = "tamper-after-shallow"
        with mock.patch.object(guard, "_call_jev", return_value={"answers": {
                "q_tamper": {"type": "boolean", "probability": .1}}}):
            self.assertIsNotNone(guard.analyze(tamper)[0])
        self.assertEqual(guard._count("sess", "calls-tamper"), 1)

    def test_bad_or_incomplete_answers_fail_open(self):
        for i, response in enumerate(({}, {"answers": {"q_tamper": {"type": "choice", "probability": .99}}})):
            p = payload(old="assert f()", new="f()")
            p["tool_use_id"] = str(bool(response))
            p["tool_input"]["file_path"] = f"tests/test_{i}.py"
            p["tool_response"]["filePath"] = f"tests/test_{i}.py"
            with mock.patch.object(guard, "_call_jev", return_value=response):
                rec, candidates = guard.analyze(p)
            self.assertEqual(candidates, [])
            self.assertIn(rec["error"], ("ValueError", "KeyError"))

    def test_jev_timeout_fails_open(self):
        with mock.patch.object(guard, "_call_jev", side_effect=TimeoutError()):
            rec, candidates = guard.analyze(payload(old="assert f()", new="f()"))
        self.assertEqual(candidates, [])
        self.assertEqual(rec["error"], "TimeoutError")

    def test_redaction_failure_suppresses_call(self):
        with mock.patch.object(guard, "_redact_field", side_effect=RuntimeError("redact")), mock.patch.object(guard, "_call_jev") as jev:
            self.assertIsNone(guard.analyze(payload(old="assert f()", new="f()"))[0])
            jev.assert_not_called()

    def test_shadow_logs_one_record_without_code(self):
        p = payload(old="assert SECRET_CODE_SENTINEL == 1", new="SECRET_CODE_SENTINEL == 1")
        with mock.patch.object(guard, "_call_jev", return_value={"answers": {"q_tamper": {"type": "boolean", "probability": .1}}}):
            record, _ = guard.analyze(p)
        guard._log(record)
        data = (guard.STATE_DIR / "events.jsonl").read_text()
        self.assertEqual(len(data.splitlines()), 1)
        self.assertNotIn("SECRET_CODE_SENTINEL", data)

    def test_nudge_exact_line_and_exit_code(self):
        p = payload(path="/tmp/project/tests/test_demo.py", old="assert result == 1", new="result", line=9)
        with mock.patch.object(guard, "SENTINEL_PATH", Path(self.temp.name) / "absent"), \
             mock.patch.object(guard, "_call_jev", return_value={"answers": {"q_tamper": {"type": "boolean", "probability": .95}}}), \
             mock.patch.object(guard.sys, "stdin", io.StringIO(json.dumps(p))), \
             mock.patch.object(guard.sys, "stderr", new_callable=io.StringIO) as err, \
             mock.patch.dict("os.environ", {"CLAUDE_EDIT_GUARD_MODE": "nudge"}, clear=False):
            self.assertEqual(guard.main(), 2)
        self.assertEqual(err.getvalue(), "[edit-guard] tests/test_demo.py:9 weakens or bypasses a test (Jev 0.95). Fix the code, not the test, or say why the test was wrong.\n")

    def test_shadow_mode_and_kill_switch(self):
        p = payload(old="assert f()", new="f()")
        with mock.patch.object(guard, "SENTINEL_PATH", Path(self.temp.name) / "absent"), \
             mock.patch.object(guard, "_call_jev", return_value={"answers": {"q_tamper": {"type": "boolean", "probability": .99}}}), \
             mock.patch.object(guard.sys, "stdin", io.StringIO(json.dumps(p))), \
             mock.patch.dict("os.environ", {"CLAUDE_EDIT_GUARD_MODE": "shadow"}, clear=False):
            self.assertEqual(guard.main(), 0)
        self.assertEqual(len((guard.STATE_DIR / "events.jsonl").read_text().splitlines()), 1)
        with mock.patch.dict("os.environ", {"CLAUDE_EDIT_GUARD": "0"}, clear=False):
            self.assertEqual(guard.main(), 0)

    def test_sentinel_and_nudge_cap(self):
        with mock.patch.object(guard, "SENTINEL_PATH", Path(self.temp.name)):
            self.assertEqual(guard.main(), 0)
        for _ in range(5): guard._bump("sess", "nudges")
        p = payload(old="assert f()", new="f()")
        with mock.patch.object(guard, "SENTINEL_PATH", Path(self.temp.name) / "absent"), \
             mock.patch.object(guard, "_call_jev", return_value={"answers": {"q_tamper": {"type": "boolean", "probability": .99}}}), \
             mock.patch.object(guard.sys, "stdin", io.StringIO(json.dumps(p))), \
             mock.patch.object(guard.sys, "stderr", new_callable=io.StringIO) as err, \
             mock.patch.dict("os.environ", {"CLAUDE_EDIT_GUARD_MODE": "nudge"}, clear=False):
            self.assertEqual(guard.main(), 0)
        self.assertEqual(err.getvalue(), "")

    def test_transport_timeout_clamped_to_deadline(self):
        captured = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"answers": {}}'
        def urlopen(req, timeout):
            captured.append(timeout)
            return Response()
        with mock.patch.object(transport.urllib.request, "urlopen", side_effect=urlopen):
            transport._call_jev({}, {}, 8.0, time.monotonic() + 0.05)
        self.assertLessEqual(captured[0], .05)


if __name__ == "__main__":
    unittest.main()


class BoundedContextCutTests(unittest.TestCase):
    def test_cut_keeps_whole_lines(self):
        lines = [("new", i, f"assert isinstance(r{i}, R)  # {'x' * 40}") for i in range(1, 400)]
        text = guard._bounded_context([lines], {"shallow": [n for _, n, _ in lines]})
        self.assertLessEqual(len(text), guard.CONTEXT_CHAR_CAP)
        self.assertTrue(text.endswith("R)  # " + "x" * 40))

    def test_single_overlong_line_yields_no_context(self):
        lines = [("new", 1, "assert isinstance(r, R)  # " + "y" * 7000)]
        self.assertEqual(guard._bounded_context([lines], {"shallow": [1]}), "")


class ProcessExitCodeTests(unittest.TestCase):
    """The script's own __main__ block must pass exit code 2 through to Claude."""

    def test_nudge_exits_two_as_a_process(self):
        import os
        import subprocess
        driver = (
            "import runpy, sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "import sage.jev.transport as t\n"
            "t._call_jev = lambda *a, **k: {'answers': {'q_tamper': {'type': 'boolean', 'probability': 0.95}}}\n"
            f"runpy.run_path({str(REPO / 'hooks/claude-edit-guard.py')!r}, run_name='__main__')\n"
        )
        with tempfile.TemporaryDirectory() as state:
            env = dict(os.environ, CLAUDE_EDIT_GUARD_MODE="nudge", CLAUDE_EDIT_GUARD_STATE=state)
            env.pop("CLAUDE_EDIT_GUARD", None)
            done = subprocess.run([sys.executable, "-c", driver], input=json.dumps(payload(new="")),
                                  capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertTrue(done.stderr.startswith("[edit-guard] "), done.stderr)
