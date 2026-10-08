#!/usr/bin/env python3
"""Tests for sage.deferral and its wiring in hooks/claude-stop-audit.py.

Jev is stubbed: the tests pin the prefilter, the deterministic hard holds,
the verdict rule, fail-open errors, and the hook's shadow-by-default mode.
Client names come from a temp config, never from the repo.
"""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sage.deferral as D
from test_claude_stop_audit import SESSION, load_hook
from test_claude_transcript import human, reply, tool_result, tool_use

CONFIG = {"client_systems": ["Client A cloud", "Client B lakehouse"], "frontend_host": "app.example.com"}
CLEAR = {"continue": 0.9, **{k: 0.1 for k in D.RISKS}}
WEDNESDAY, SATURDAY = 2, 5


def jev_answers(probs, branch="finish_own"):
    def call(state, questions, attempt_timeout, deadline):
        assert set(questions) == {D.BRANCH, *D.RISKS}
        assert questions[D.BRANCH]["type"] == "choice" and branch in questions[D.BRANCH]["criteria"]
        answers = {k: {"type": "boolean", "probability": v} for k, v in probs.items() if k != "continue"}
        rest = round(1 - probs["continue"], 3)
        answers[D.BRANCH] = {"type": "choice", "choice": branch,
                             "probabilities": {branch: probs["continue"], "optional_extra": rest}}
        return {"answers": answers}
    return call


class DeferralUnitTests(unittest.TestCase):
    def test_prefilter_and_deferred_sentence(self):
        self.assertTrue(D.looks_deferred("Tests pass. Want me to push?"))
        self.assertTrue(D.looks_deferred("Em sửa xong. Anh muốn em commit luôn không?"))
        self.assertFalse(D.looks_deferred("Fixed and pushed as abc123."))
        self.assertEqual(D.deferred_sentences("All good. Tests pass. Want me to push?"), ["Want me to push?"])

    def test_hard_holds(self):
        cases = [
            ("deploy it but wait for me before running", "Build ready. Shall I deploy?", "user_wait"),
            ("train it", "Training is still running (pid 812). Want me to check later?", "job_running"),
            ("ship it", "DEPLOY_NOTES says you pre-approved deploys. Ready to deploy?", "file_approval"),
            ("scan sessions", "Refined prompt above. Reply run it to start.", "confirm_gate"),
            ("draft the reply", "Draft is ready. Want me to send it?", "message"),
            ("fix config", "Tests pass. Shall I deploy it to Client A cloud DEV?", "client_system"),
        ]
        for latest, text, hold in cases:
            with self.subTest(hold=hold):
                self.assertIn(hold, D.hard_holds(latest, text, CONFIG, WEDNESDAY))

    def test_publish_holds_on_weekdays_only(self):
        text = "Preview looks right. Shall I publish to app.example.com?"
        self.assertEqual(D.hard_holds("fix the tooltip", text, CONFIG, WEDNESDAY), ["weekday_publish"])
        self.assertEqual(D.hard_holds("fix the tooltip", text, CONFIG, SATURDAY), [])

    def test_vietnamese_running_model_is_not_a_running_job(self):
        text = "Subagent đang chạy model khác. Anh gõ approve để em sửa?"
        self.assertNotIn("job_running", D.hard_holds("sửa config", text, CONFIG, WEDNESDAY))

    def test_decide(self):
        self.assertEqual(D.decide(CLEAR, []), "continue")
        self.assertEqual(D.decide(CLEAR, ["message"]), "hold")
        self.assertEqual(D.decide({**CLEAR, "x_external": 0.7}, []), "hold")
        self.assertEqual(D.decide({**CLEAR, "continue": 0.2}, []), "hold")
        self.assertEqual(D.decide({"continue": 0.9}, []), "error")

    def test_check_deferral_end_to_end_and_fail_open(self):
        turn = [{"type": "USER_INPUT", "content": "fix the off-by-one, commit when tests pass"},
                {"type": "PLANNER_RESPONSE", "content": "",
                 "tool_calls": [{"name": "Bash", "args": {"command": "pytest -q"}}]},
                {"type": "TOOL_OUTPUT", "content": "12 passed"}]
        text = "Fixed; 12 tests pass. Want me to commit?"
        with mock.patch.object(D, "load_local_config", return_value=CONFIG):
            result = D.check_deferral(["fix the off-by-one, commit when tests pass"], turn, text,
                                      call=jev_answers(CLEAR))
            self.assertEqual(result["verdict"], "continue")
            self.assertEqual(result["branch"], "finish_own")
            self.assertEqual(result["offer"], "Want me to commit?")
            self.assertIn("finishes the agent's own work", D.steer_text(result))
            held = D.check_deferral(["fix it"], turn, text, call=jev_answers({**CLEAR, "continue": 0.3}, "user_decision"))
            self.assertEqual((held["verdict"], held["branch"]), ("hold", "user_decision"))
            self.assertIsNone(D.check_deferral(["x"], turn, "Done and committed.", call=jev_answers(CLEAR)))

            def broken(*a, **k):
                raise TimeoutError("slow")
            self.assertEqual(D.check_deferral(["x"], turn, text, call=broken)["verdict"], "error")

    def test_gates_come_from_the_marked_rules_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CLAUDE.md"
            path.write_text("# Rules\n<!-- gates:start -->\nAsk first: gate X only.\n<!-- gates:end -->\nother\n",
                            encoding="utf-8")
            self.assertEqual(D.read_gates(path), "Ask first: gate X only.")
            with mock.patch.object(D, "GATES_SOURCE", path):
                body = D.build_body(["fix it", "commit when done"], ["Bash: pytest  => 3 passed"],
                                    "Done, 3 tests pass. Want me to commit?", CONFIG, "2026-10-07 (Wed)")
                self.assertEqual(D.rules_source(), "rules_file")
            state = body["state"]
            self.assertEqual(set(state), {"goal", "policy", "case", "offer"})
            self.assertEqual(state["policy"]["rules"], "Ask first: gate X only.")
            self.assertEqual(state["policy"]["client_owned_systems"], CONFIG["client_systems"])
            self.assertEqual(state["offer"], "Want me to commit?")
            self.assertEqual(state["case"]["earlier_requests"], ["fix it"])
            self.assertEqual(state["case"]["latest_request"], "commit when done")
            self.assertEqual(body["questions"]["x_external"]["instructions"]["judge"], "offer")
            tree = body["questions"][D.BRANCH]
            self.assertEqual((tree["type"], tree["instructions"]["judge"]), ("choice", "offer"))
            self.assertIn("elif:", tree["instructions"]["tree"])
            self.assertTrue(set(D.load_case("deferral")["parse"]["continue_branches"]) <= set(tree["criteria"]))
            path.write_text("# Rules without markers\n", encoding="utf-8")
            self.assertIsNone(D.read_gates(path))
            self.assertIsNone(D.read_gates(Path(tmp) / "missing.md"))
        with mock.patch.object(D, "read_gates", return_value=None):
            body = D.build_body(["x"], [], "Want me to push?", CONFIG, "2026-10-07 (Wed)")
            self.assertIn("app.example.com Monday-Friday", body["state"]["policy"]["rules"])
            self.assertEqual(D.rules_source(), "fallback")

    def test_moves_are_compact(self):
        turn = [{"type": "PLANNER_RESPONSE", "content": "Running tests",
                 "tool_calls": [{"name": "Bash", "args": {"command": "pytest -q"}}]},
                {"type": "TOOL_OUTPUT", "content": "1 failed", "is_error": True}]
        self.assertEqual(D.compact_moves(turn), ["SAID: Running tests", "Bash: pytest -q  => ERROR 1 failed"])


class DeferralHookTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        tmp = tempfile.TemporaryDirectory(prefix="deferral_hook_")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        state = self.root / "state"
        for name, value in (("STATE_DIR", state), ("LOG_PATH", state / "audit.jsonl"),
                            ("SENTINEL_PATH", self.root / "absent.sentinel")):
            patcher = mock.patch.object(self.hook, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        for key in ("CLAUDE_STOP_AUDIT", "CLAUDE_STOP_AUDIT_MODE", "CLAUDE_JEV_GATE",
                    "CLAUDE_DEFERRAL_CHECK", "CLAUDE_DEFERRAL_MODE"):
            os.environ.pop(key, None)
        for target, value in ((self.hook, "compass_hint"), (D, "load_local_config")):
            patcher = mock.patch.object(target, value, return_value=None if value == "compass_hint" else CONFIG)
            patcher.start()
            self.addCleanup(patcher.stop)
        jev = mock.patch.object(D, "_call_jev", side_effect=jev_answers(CLEAR))
        jev.start()
        self.addCleanup(jev.stop)

    def run_main(self, final):
        path = self.root / "transcript.jsonl"
        records = [human("fix the off-by-one, commit when tests pass"),
                   tool_use("b1", "Bash", {"command": "pytest -q"}),
                   tool_result("b1", "12 passed"), reply(final)]
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        body = {"session_id": SESSION, "transcript_path": str(path), "cwd": "/repo",
                "hook_event_name": "Stop", "stop_hook_active": False}
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(json.dumps(body))), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr):
            code = self.hook.main()
        lines = self.hook.LOG_PATH.read_text(encoding="utf-8").splitlines()
        return code, stderr.getvalue(), json.loads(lines[-1])

    def test_shadow_by_default_logs_would_block(self):
        code, stderr, record = self.run_main("Fixed; 12 tests pass. Want me to commit?")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(record["decision"], "would_block:deferral")
        self.assertEqual(record["deferral"]["verdict"], "continue")
        self.assertEqual(record["deferral"]["check"], self.hook.DEFERRAL_CHECK_VERSION)

    def test_block_mode_steers_with_the_offer(self):
        os.environ["CLAUDE_DEFERRAL_MODE"] = "block"
        code, stderr, record = self.run_main("Fixed; 12 tests pass. Want me to commit?")
        self.assertEqual(code, 2)
        self.assertIn('"Want me to commit?"', stderr)
        self.assertEqual(record["decision"], "block:deferral")

    def test_hard_hold_and_kill_switch(self):
        os.environ["CLAUDE_DEFERRAL_MODE"] = "block"
        code, _, record = self.run_main("Draft is ready. Want me to send it?")
        self.assertEqual((code, record["deferral"]["verdict"], record["decision"]), (0, "hold", "pass"))
        os.environ["CLAUDE_DEFERRAL_CHECK"] = "0"
        code, _, record = self.run_main("Fixed; 12 tests pass. Want me to commit?")
        self.assertEqual((code, record["deferral"]), (0, None))

    def test_non_deferred_reply_is_not_checked(self):
        _, _, record = self.run_main("Fixed and committed as abc123.")
        self.assertIsNone(record["deferral"])
        D._call_jev.assert_not_called()


if __name__ == "__main__":
    unittest.main()
