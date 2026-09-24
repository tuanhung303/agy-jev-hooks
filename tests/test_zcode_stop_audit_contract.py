#!/usr/bin/env python3
"""Opaque-box contract tests for hooks/zcode-stop-audit.py.

Covers the Stop payload guards, the rollout-to-steps adapter, the
suspicion-to-exit-code contract (suspicion = exit 2 + stderr steer), the
per-session steer cap, and fail-open paths. The Jev/Compass gate is stubbed
here; ZCode differs from Qoder only in the transcript side.
"""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "zcode-stop-audit.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("zcode_stop_audit_contract_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookContractTestCase(unittest.TestCase):
    """Shared plumbing: hermetic state dir and stubbed gates."""

    def setUp(self):
        self.hook = load_hook()
        self._state = tempfile.TemporaryDirectory(prefix="zcode_stop_audit_test_")
        self.addCleanup(self._state.cleanup)
        self.state_dir = Path(self._state.name)
        patcher = mock.patch.object(self.hook, "STATE_DIR", self.state_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hook.LOG_PATH = self.state_dir / "audit.log"
        self.gate = mock.patch.object(
            self.hook, "jev_verifier_hint", return_value=None)
        self.gate_mock = self.gate.start()
        self.addCleanup(self.gate.stop)
        self.claims = mock.patch.object(
            self.hook, "claim_contract_hint_for", return_value=None)
        self.claims_mock = self.claims.start()
        self.addCleanup(self.claims.stop)
        self.rollout = mock.patch.object(
            self.hook, "resolve_rollout", return_value=self.state_dir / "rollout.jsonl")
        self.rollout_mock = self.rollout.start()
        self.addCleanup(self.rollout.stop)
        self.snapshot = mock.patch.object(
            self.hook, "steps_from_rollout",
            return_value=([{"type": "USER_INPUT", "content": "prompt",
                            "created_at": "2026-09-22T10:00:00+00:00"}],
                          "reply", "2026-09-22T10:00:00Z"))
        self.snapshot_mock = self.snapshot.start()
        self.addCleanup(self.snapshot.stop)

    def run_main(self, payload=None, raw_stdin=None):
        """Run hook.main() with canned stdin; return (exit_code, stderr_text)."""
        if raw_stdin is None:
            raw_stdin = json.dumps(payload if payload is not None else {})
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(raw_stdin)), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr):
            code = self.hook.main()
        return code, stderr.getvalue()

    @staticmethod
    def payload(**overrides):
        base = {"session_id": "sess_22222222-2222-4222-8222-222222222222",
                "cwd": "/tmp/project"}
        base.update(overrides)
        return base


class GuardTests(HookContractTestCase):
    """Every early-exit guard must return 0 without calling the gates."""

    def test_env_kill_switches_exit_zero(self):
        for var in ("ZCODE_SAGE_DISABLED", "ZCODE_STOP_AUDIT"):
            with self.subTest(var=var), mock.patch.dict(self.hook.os.environ, {var: "1"}):
                code, _ = self.run_main(self.payload())
                self.assertEqual(code, 0)
                self.gate_mock.assert_not_called()
                self.claims_mock.assert_not_called()

    def test_stop_hook_active_short_circuits(self):
        code, _ = self.run_main(self.payload(stop_hook_active=True))
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_invalid_stdin_fails_open(self):
        code, _ = self.run_main(raw_stdin="{not json")
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_missing_session_id_fails_open(self):
        code, _ = self.run_main({"cwd": "/tmp/project"})
        self.assertEqual(code, 0)
        self.rollout_mock.assert_not_called()

    def test_missing_rollout_fails_open(self):
        self.rollout_mock.return_value = None
        code, _ = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.snapshot_mock.assert_not_called()

    def test_unfinished_turn_fails_open(self):
        self.snapshot_mock.return_value = (None, None, None)
        code, _ = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()


class SteerCapTests(HookContractTestCase):
    """MAX_STEERS_PER_SESSION bounds the exit-2 loop."""

    def _preseed(self, session, count):
        (self.state_dir / f"{session}.steers").write_text(str(count))

    def test_at_cap_skips_gate(self):
        session = self.payload()["session_id"]
        self._preseed(session, self.hook.MAX_STEERS_PER_SESSION)
        code, _ = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_below_cap_runs_gate(self):
        session = self.payload()["session_id"]
        self._preseed(session, 1)
        self.gate_mock.return_value = None
        code, _ = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.gate_mock.assert_called_once()

    def test_suspicion_bumps_counter_once(self):
        session = self.payload()["session_id"]
        self.gate_mock.return_value = "FakeCategory: criterion text"
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        counter = self.state_dir / f"{session}.steers"
        self.assertEqual(counter.read_text(), "1")
        self.assertIn("[zcode-stop-audit] FakeCategory", err)

    def test_second_suspicion_at_cap_stops(self):
        session = self.payload()["session_id"]
        self._preseed(session, self.hook.MAX_STEERS_PER_SESSION - 1)
        self.gate_mock.return_value = "FakeCategory: criterion text"
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("[zcode-stop-audit]", err)
        code, _ = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(self.gate_mock.call_count, 1)


class SteerSanitizationTests(HookContractTestCase):
    """One sanitized string is logged and emitted; sanitizer failure is silent."""

    def test_secret_never_reaches_stderr_or_log(self):
        secret = "SYNTHETIC_CANARY_" + "z" * 24
        self.gate_mock.return_value = f"not_verified: check it Evidence: password:{secret}"
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        self.assertNotIn(secret, err)
        self.assertIn("[redacted]", err)
        action = err.strip()[len("[zcode-stop-audit] "):]
        logged = self.hook.LOG_PATH.read_text()
        self.assertNotIn(secret, logged)
        self.assertIn(action, logged)

    def test_sanitizer_failure_exits_silently(self):
        session = self.payload()["session_id"]
        self.gate_mock.return_value = "not_verified: check it"
        with mock.patch.object(self.hook, "_redact_secrets", side_effect=RuntimeError("boom")):
            code, err = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertFalse((self.state_dir / f"{session}.steers").exists())
        self.assertIn("suppressed", self.hook.LOG_PATH.read_text())

    def test_missing_sanitizer_exits_silently(self):
        self.gate_mock.return_value = "not_verified: check it"
        with mock.patch.object(self.hook, "_redact_secrets", None):
            code, err = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")


class GateOrderTests(HookContractTestCase):
    """Claim contract outranks Compass; both feed the same steer channel."""

    def test_claim_hint_wins_and_bounds_text(self):
        self.claims_mock.return_value = "jev_compass not_verified: claim" + " x" * 800
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        steer = err.strip()
        self.assertTrue(steer.startswith("[zcode-stop-audit] jev_compass not_verified"))
        self.assertLessEqual(len(steer), len("[zcode-stop-audit] ") + self.hook.STEER_TEXT_LIMIT)
        self.gate_mock.assert_not_called()

    def test_claim_contract_survives_jev_kill_switch(self):
        # The kill switch cuts the remote compass call only; the local claim
        # contract keeps steering.
        self.claims_mock.return_value = "jev_compass not_verified: claim"
        with mock.patch.dict(self.hook.os.environ, {"ZCODE_JEV_GATE": "0"}):
            code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("not_verified", err)
        self.gate_mock.assert_not_called()

    def test_steps_file_passed_to_gates(self):
        self.gate_mock.return_value = None
        self.run_main(self.payload())
        steps_arg = self.gate_mock.call_args[0][2]
        self.assertTrue(str(steps_arg).endswith(".steps.jsonl"))
        written = self.state_dir / f"{self.payload()['session_id']}.steps.jsonl"
        line = json.loads(written.read_text().splitlines()[0])
        self.assertEqual(line["type"], "USER_INPUT")

    def test_steps_write_failure_fails_open(self):
        self.state_dir.chmod(0o500)
        try:
            code, _ = self.run_main(self.payload())
            self.assertEqual(code, 0)
            self.gate_mock.assert_not_called()
        finally:
            self.state_dir.chmod(0o700)


class RolloutAdapterTests(unittest.TestCase):
    """steps_from_rollout replays the newest finished snapshot into sage steps."""

    def setUp(self):
        self.hook = load_hook()
        self._tmp = tempfile.TemporaryDirectory(prefix="zcode_rollout_test_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _write_rollout(self, lines):
        path = self.tmp / "model-io-sess_s1.jsonl"
        path.write_text("\n".join(json.dumps(line) if isinstance(line, dict) else line
                                  for line in lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def snapshot(finish="stop", text="final reply"):
        return {
            "type": "model_io",
            "startedAt": "2026-09-22T10:00:05.000Z",
            "request": {"messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "do the work"},
                {"role": "assistant", "content": [{"type": "reasoning", "text": "think"}],
                 "toolCalls": [{"id": "c1", "name": "Bash",
                                "input": {"command": "pytest -q"}}]},
                {"role": "tool", "content": "1 passed", "toolCallId": "c1",
                 "toolName": "Bash", "isError": False},
            ]},
            "response": {"finishReason": finish, "text": text, "toolCalls": []},
        }

    def test_full_snapshot_to_steps(self):
        path = self._write_rollout([self.snapshot()])
        steps, reply, started = self.hook.steps_from_rollout(path)
        kinds = [s["type"] for s in steps]
        self.assertEqual(kinds, ["USER_INPUT", "PLANNER_RESPONSE", "TOOL_OUTPUT", "PLANNER_RESPONSE"])
        self.assertEqual(reply, "final reply")
        self.assertEqual(started, "2026-09-22T10:00:05.000Z")
        self.assertEqual(steps[0]["content"], "do the work")
        self.assertEqual(steps[1]["tool_calls"][0]["name"], "Bash")
        self.assertEqual(steps[1]["tool_calls"][0]["args"], {"command": "pytest -q"})
        self.assertEqual(steps[2]["tool_call_id"], "c1")
        self.assertIs(steps[2]["isError"], False)
        self.assertEqual(steps[3]["content"], "final reply")

    def test_reasoning_text_excluded(self):
        path = self._write_rollout([self.snapshot()])
        steps, _, _ = self.hook.steps_from_rollout(path)
        self.assertNotIn("think", steps[1]["content"])

    def test_mid_turn_finish_reason_fails_open(self):
        path = self._write_rollout([self.snapshot(finish="tool-calls")])
        self.assertEqual(self.hook.steps_from_rollout(path), (None, None, None))

    def test_malformed_tail_line_falls_back(self):
        path = self.tmp / "model-io-sess_s1.jsonl"
        path.write_text(json.dumps(self.snapshot()) + "\n{truncated", encoding="utf-8")
        steps, reply, _ = self.hook.steps_from_rollout(path)
        self.assertEqual(reply, "final reply")

    def test_timestamps_increase(self):
        path = self._write_rollout([self.snapshot()])
        steps, _, _ = self.hook.steps_from_rollout(path)
        stamps = [s["created_at"] for s in steps]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(stamps))

    def test_empty_or_absent_messages_fail_open(self):
        empty = self.snapshot()
        empty["request"]["messages"] = []
        path = self._write_rollout([empty])
        self.assertEqual(self.hook.steps_from_rollout(path), (None, None, None))


class ResolveRolloutTests(unittest.TestCase):
    """Rollout lookup: explicit path, sess_ prefix stripping, no wild guessing."""

    def setUp(self):
        self.hook = load_hook()
        self._tmp = tempfile.TemporaryDirectory(prefix="zcode_resolve_test_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = mock.patch.object(self.hook, "ROLLOUT_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_explicit_path_wins(self):
        explicit = self.tmp / "explicit.jsonl"
        explicit.write_text("{}\n", encoding="utf-8")
        found = self.hook.resolve_rollout({"transcript_path": str(explicit)})
        self.assertEqual(found, explicit)

    def test_sess_prefix_stripped(self):
        target = self.tmp / "model-io-sess_abc.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        found = self.hook.resolve_rollout({"session_id": "sess_abc"})
        self.assertEqual(found, target)

    def test_unknown_session_returns_none(self):
        self.assertIsNone(self.hook.resolve_rollout({"session_id": "missing"}))
        self.assertIsNone(self.hook.resolve_rollout({}))


class SnippetTests(unittest.TestCase):
    """Prompt tail from the last USER_INPUT; reply stays whole; both required."""

    def setUp(self):
        self.hook = load_hook()

    def test_last_user_input_wins(self):
        steps = [
            {"type": "USER_INPUT", "content": "first"},
            {"type": "USER_INPUT", "content": "second"},
            {"type": "PLANNER_RESPONSE", "content": "x"},
        ]
        prompt, reply = self.hook.last_turn_snippets(steps, "the reply")
        self.assertEqual(prompt, "second")
        self.assertEqual(reply, "the reply")

    def test_prompt_tail_bounded(self):
        steps = [{"type": "USER_INPUT", "content": "p" * 2000}]
        prompt, _ = self.hook.last_turn_snippets(steps, "reply")
        self.assertEqual(len(prompt), self.hook.SNIPPET_LIMIT)

    def test_empty_reply_returns_none(self):
        steps = [{"type": "USER_INPUT", "content": "p"}]
        self.assertIsNone(self.hook.last_turn_snippets(steps, "  "))


if __name__ == "__main__":
    unittest.main()
