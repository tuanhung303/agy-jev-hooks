#!/usr/bin/env python3
"""Opaque-box contract tests for hooks/qoder-stop-audit.py.

Covers the Stop payload guards, the suspicion-to-exit-code contract (suspicion
= exit 2 + stderr steer), the per-session steer cap, fail-open paths, and
transcript snippet extraction. The Jev/Compass gate is stubbed here; its wiring
lives in test_qoder_stop_audit_jev.py.
"""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "qoder-stop-audit.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("qoder_stop_audit_contract_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookContractTestCase(unittest.TestCase):
    """Shared plumbing: hermetic state dir and a stubbed gate."""

    def setUp(self):
        self.hook = load_hook()
        self._state = tempfile.TemporaryDirectory(prefix="qoder_stop_audit_test_")
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

    def run_main(self, payload=None, raw_stdin=None):
        """Run hook.main() with canned stdin; return (exit_code, stderr_text)."""
        if raw_stdin is None:
            raw_stdin = json.dumps(payload if payload is not None else {})
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(raw_stdin)), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr), \
                mock.patch.object(self.hook, "last_turn_snippets",
                                  return_value=("prompt", "reply")) as snippets:
            code = self.hook.main()
        self.last = {"snippets": snippets}
        return code, stderr.getvalue()

    @staticmethod
    def payload(**overrides):
        base = {"session_id": "11111111-1111-4111-8111-111111111111",
                "cwd": "/tmp/project",
                "transcript_path": "/tmp/transcript.jsonl"}
        base.update(overrides)
        return base


class GuardTests(HookContractTestCase):
    """Every early-exit guard must return 0 without calling the gate."""

    def test_env_kill_switches_exit_zero(self):
        for var in ("QODER_SAGE_DISABLED", "QODER_STOP_AUDIT"):
            with self.subTest(var=var), mock.patch.dict(self.hook.os.environ, {var: "1"}):
                code, _ = self.run_main(self.payload())
                self.assertEqual(code, 0)
                self.gate_mock.assert_not_called()

    def test_stop_hook_active_short_circuits(self):
        code, _ = self.run_main(self.payload(stop_hook_active=True))
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_invalid_stdin_fails_open(self):
        code, _ = self.run_main(raw_stdin="{not json")
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_missing_session_id_fails_open(self):
        code, _ = self.run_main({"transcript_path": "/tmp/t.jsonl"})
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_missing_transcript_path_fails_open(self):
        code, _ = self.run_main({"session_id": "abc"})
        self.assertEqual(code, 0)
        self.gate_mock.assert_not_called()

    def test_missing_fields_are_logged(self):
        code, _ = self.run_main({})
        self.assertEqual(code, 0)
        self.assertIn("payload missing fields", self.hook.LOG_PATH.read_text())

    def test_snippets_none_skips_gate(self):
        with mock.patch.object(self.hook.sys, "stdin",
                               new=io.StringIO(json.dumps(self.payload()))), \
                mock.patch.object(self.hook.sys, "stderr", new=io.StringIO()), \
                mock.patch.object(self.hook, "last_turn_snippets", return_value=None):
            self.assertEqual(self.hook.main(), 0)
        self.gate_mock.assert_not_called()

    def test_gate_receives_snippets_and_transcript_path(self):
        self.run_main(self.payload())
        self.gate_mock.assert_called_once_with(
            "prompt", "reply", self.payload()["transcript_path"])


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
        self.assertIn("[qoder-stop-audit] FakeCategory", err)

    def test_second_suspicion_at_cap_stops(self):
        session = self.payload()["session_id"]
        self._preseed(session, self.hook.MAX_STEERS_PER_SESSION - 1)
        self.gate_mock.return_value = "FakeCategory: criterion text"
        code, _ = self.run_main(self.payload())
        self.assertEqual(code, 2)
        self.assertEqual(
            (self.state_dir / f"{session}.steers").read_text(),
            str(self.hook.MAX_STEERS_PER_SESSION))

    def test_no_suspicion_exits_zero_without_stderr(self):
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("PASS", self.hook.LOG_PATH.read_text())

    def test_long_hint_is_clamped(self):
        session = self.payload()["session_id"]
        self.gate_mock.return_value = "x" * 5000
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        self.assertLessEqual(len(err),
                             self.hook.STEER_TEXT_LIMIT + len("[qoder-stop-audit] \n") + 1)
        self.assertEqual(
            (self.state_dir / f"{session}.steers").read_text(), "1")


class SteerSanitizationTests(HookContractTestCase):
    """One sanitized string is logged, emitted, and returned; failure is silent."""

    def test_secret_never_reaches_stderr_or_log(self):
        secret = "SYNTHETIC_CANARY_" + "z" * 24
        self.gate_mock.return_value = f"not_verified: check it Evidence: password:{secret}"
        code, err = self.run_main(self.payload())
        self.assertEqual(code, 2)
        self.assertNotIn(secret, err)
        self.assertIn("[redacted]", err)
        action = err.strip()[len("[qoder-stop-audit] "):]
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

    def test_blank_hint_after_sanitization_is_suppressed(self):
        session = self.payload()["session_id"]
        self.gate_mock.return_value = "   "
        with mock.patch.object(self.hook, "_redact_secrets", side_effect=lambda text: ""):
            code, err = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertFalse((self.state_dir / f"{session}.steers").exists())


class TranscriptSnippetsTests(unittest.TestCase):
    """last_turn_snippets must pull the last human prompt + last reply."""

    def setUp(self):
        self.hook = load_hook()
        self._tmp = tempfile.TemporaryDirectory(prefix="qoder_transcript_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "t.jsonl"

    def _write(self, entries):
        with self.path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def test_extracts_last_human_prompt_and_last_reply(self):
        self._write([
            {"type": "user", "origin": {"kind": "human"},
             "message": {"content": [{"type": "text", "text": "first ask"}]}},
            {"type": "assistant",
             "message": {"content": [{"type": "text", "text": "first answer"}]}},
            {"type": "user", "origin": {"kind": "hook"},
             "message": {"content": [{"type": "text", "text": "hook noise"}]}},
            {"type": "user", "origin": {"kind": "human"},
             "message": {"content": [{"type": "text", "text": "real ask"}]}},
            {"type": "assistant",
             "message": {"content": [{"type": "text", "text": "real answer"}]}},
        ])
        prompt, reply = self.hook.last_turn_snippets(str(self.path))
        self.assertEqual(prompt, "real ask")
        self.assertEqual(reply, "real answer")

    def test_no_reply_returns_none(self):
        self._write([
            {"type": "user", "origin": {"kind": "human"},
             "message": {"content": [{"type": "text", "text": "ask only"}]}},
        ])
        self.assertIsNone(self.hook.last_turn_snippets(str(self.path)))

    def test_unreadable_path_returns_none(self):
        self.assertIsNone(self.hook.last_turn_snippets("/nonexistent/t.jsonl"))

    def test_reply_stays_whole_for_style_detection(self):
        # Casual category judges length and tone; a tail slice cannot.
        self._write([
            {"type": "assistant",
             "message": {"content": [{"type": "text", "text": "x" * 5000}]}},
        ])
        _, reply = self.hook.last_turn_snippets(str(self.path))
        self.assertEqual(len(reply), 5000)


if __name__ == "__main__":
    unittest.main()
