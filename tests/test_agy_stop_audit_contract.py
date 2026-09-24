#!/usr/bin/env python3
"""Contract and safety tests for hooks/agy-stop-audit.py.

Covers Antigravity Stop payload guards, token-drain loop protection (fullyIdle,
terminationReason, steer cap), fail-open paths, snippet extraction from Antigravity
native transcripts, and the {"decision": "continue", "reason": "..."} output contract.
"""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "agy-stop-audit.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("agy_stop_audit_contract_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookContractTestCase(unittest.TestCase):
    """Hermetic state dir and stubbed gates for agy-stop-audit."""

    def setUp(self):
        self.hook = load_hook()
        self._state = tempfile.TemporaryDirectory(prefix="agy_stop_audit_test_")
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

    def run_main(self, payload=None, raw_stdin=None):
        """Run hook.main() with canned stdin; return (exit_code, parsed_stdout)."""
        if raw_stdin is None:
            raw_stdin = json.dumps(payload if payload is not None else {})
        stdout = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(raw_stdin)), \
                mock.patch.object(self.hook.sys, "stdout", new=stdout), \
                mock.patch.object(self.hook, "last_turn_snippets",
                                  return_value=("prompt text", "reply text")) as snippets:
            code = self.hook.main()
        self.last = {"snippets": snippets}
        out_raw = stdout.getvalue().strip()
        try:
            parsed = json.loads(out_raw) if out_raw else {}
        except json.JSONDecodeError:
            parsed = None
        return code, parsed

    @staticmethod
    def payload(**overrides):
        base = {
            "conversationId": "2b6c5758-ec8b-4cb9-af82-3f2b0a0f979c",
            "workspacePaths": ["/tmp/project"],
            "transcriptPath": "/tmp/transcript.jsonl",
            "terminationReason": "model_stop",
            "fullyIdle": True,
            "executionNum": 1,
        }
        base.update(overrides)
        return base


class GuardTests(HookContractTestCase):
    """Every early-exit guard must exit 0 and emit empty object {}."""

    def test_env_kill_switches(self):
        for var in ("AGY_SAGE_DISABLED", "AGY_STOP_AUDIT"):
            val = "1" if var == "AGY_SAGE_DISABLED" else "0"
            with self.subTest(var=var), mock.patch.dict(self.hook.os.environ, {var: val}):
                code, out = self.run_main(self.payload())
                self.assertEqual(code, 0)
                self.assertEqual(out, {})
                self.gate_mock.assert_not_called()

    def test_stop_hook_active_short_circuits(self):
        code, out = self.run_main(self.payload(stop_hook_active=True))
        self.assertEqual(code, 0)
        self.assertEqual(out, {})
        self.gate_mock.assert_not_called()

    def test_fully_idle_false_abstains(self):
        """Active subagents or background tasks must never be blocked by Stop hook."""
        code, out = self.run_main(self.payload(fullyIdle=False))
        self.assertEqual(code, 0)
        self.assertEqual(out, {})
        self.gate_mock.assert_not_called()
        self.assertIn("fullyIdle is False", self.hook.LOG_PATH.read_text())

    def test_non_model_stop_termination_reasons_abstain(self):
        for reason in ("error", "max_steps_exceeded", "user_cancel", "aborted", "ERROR", "USER_CANCELED"):
            with self.subTest(reason=reason):
                code, out = self.run_main(self.payload(terminationReason=reason))
                self.assertEqual(code, 0)
                self.assertEqual(out, {})
                self.gate_mock.assert_not_called()

    def test_no_tool_call_termination_reason_is_audited(self):
        """Standard Antigravity completion reason NO_TOOL_CALL must proceed to audit."""
        self.claims_mock.return_value = "test claim: no test-run receipt with exit=0"
        code, out = self.run_main(self.payload(terminationReason="NO_TOOL_CALL"))
        self.assertEqual(code, 0)
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("no test-run receipt", out.get("reason", ""))

    def test_invalid_stdin_fails_open(self):
        code, out = self.run_main(raw_stdin="not valid json")
        self.assertEqual(code, 0)
        self.assertEqual(out, {})

    def test_missing_session_id_fails_open(self):
        code, out = self.run_main({"transcriptPath": "/tmp/t.jsonl", "fullyIdle": True})
        self.assertEqual(code, 0)
        self.assertEqual(out, {})

    def test_missing_transcript_path_fails_open(self):
        code, out = self.run_main({"conversationId": "test_id", "fullyIdle": True})
        self.assertEqual(code, 0)
        self.assertEqual(out, {})


class SteerCapTests(HookContractTestCase):
    """Cap repeated steers per session to MAX_STEERS_PER_SESSION."""

    def test_steer_cap_prevents_infinite_loops(self):
        self.gate_mock.return_value = "Unverified claim: run tests"
        sid = "test-session-loop-guard"

        # Turn 1: steer 1
        code1, out1 = self.run_main(self.payload(conversationId=sid))
        self.assertEqual(code1, 0)
        self.assertEqual(out1.get("decision"), "continue")
        self.assertIn("[agy-stop-audit]", out1.get("reason", ""))

        # Turn 2: steer 2
        code2, out2 = self.run_main(self.payload(conversationId=sid))
        self.assertEqual(code2, 0)
        self.assertEqual(out2.get("decision"), "continue")

        # Turn 3: capped -> must pass with {}
        code3, out3 = self.run_main(self.payload(conversationId=sid))
        self.assertEqual(code3, 0)
        self.assertEqual(out3, {})
        self.assertIn("steer cap reached", self.hook.LOG_PATH.read_text())


class DecisionContractTests(HookContractTestCase):
    """Antigravity Stop decision and reason schema."""

    def test_clean_pass_returns_empty_object(self):
        code, out = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(out, {})

    def test_claims_hint_triggers_continue_decision(self):
        self.claims_mock.return_value = "test claim: no test-run receipt with exit=0"
        code, out = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("[agy-stop-audit]", out.get("reason", ""))
        self.assertIn("no test-run receipt", out.get("reason", ""))

    def test_compass_hint_triggers_continue_decision(self):
        self.gate_mock.return_value = "jev_compass undone: missing implementation"
        code, out = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("missing implementation", out.get("reason", ""))

    def test_long_hint_is_clamped_safely(self):
        self.gate_mock.return_value = "x" * 5000
        code, out = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(out.get("decision"), "continue")
        reason = out.get("reason", "")
        self.assertTrue(reason.startswith("[agy-stop-audit] "))
        action = reason[len("[agy-stop-audit] "):]
        self.assertLessEqual(len(action), self.hook.STEER_TEXT_LIMIT)

    def test_faked_evidence_full_hint_not_clipped(self):
        full_hint = (
            "jev_compass faked_evidence: Observed evidence fabricates success that did not occur: "
            "results hardcoded or stubbed in place of real checks (constant ok or true placeholders), "
            "failures masked or forced green (swallowed errors, forced-pass fallbacks, weakened or deleted checks), "
            "a narrowed run presented as full coverage, staged proof presented as live, quoted command output that no "
            "run produced, a machine artifact (log, report, receipt) written by hand and passed off as generated, or "
            "an old receipt replayed as a fresh run. A stub or mock used openly as a test double with real assertions "
            "is not fabrication. Evidence: $ pytest -q"
        )
        self.gate_mock.return_value = full_hint
        code, out = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(out.get("decision"), "continue")
        self.assertEqual(out.get("reason"), f"[agy-stop-audit] {full_hint}")
        self.assertIn("Evidence: $ pytest -q", out.get("reason", ""))
        self.assertIn("a machine artifact", out.get("reason", ""))


class SteerSanitizationTests(HookContractTestCase):
    """One sanitized string is logged and emitted; sanitizer failure is silent."""

    def test_secret_never_reaches_reason_or_log(self):
        secret = "SYNTHETIC_CANARY_" + "z" * 24
        self.gate_mock.return_value = f"not_verified: check it Evidence: password:{secret}"
        code, out = self.run_main(self.payload())
        reason = out.get("reason", "")
        self.assertNotIn(secret, reason)
        self.assertIn("[redacted]", reason)
        action = reason[len("[agy-stop-audit] "):]
        logged = self.hook.LOG_PATH.read_text()
        self.assertNotIn(secret, logged)
        self.assertIn(action, logged)

    def test_sanitizer_failure_abstains_silently(self):
        sid = "test-session-sanitizer-fail"
        self.gate_mock.return_value = "not_verified: check it"
        with mock.patch.object(self.hook, "_redact_secrets", side_effect=RuntimeError("boom")):
            code, out = self.run_main(self.payload(conversationId=sid))
        self.assertEqual(code, 0)
        self.assertEqual(out, {})
        self.assertFalse((self.state_dir / f"{sid}.steers").exists())
        self.assertIn("suppressed", self.hook.LOG_PATH.read_text())

    def test_missing_sanitizer_abstains_silently(self):
        self.gate_mock.return_value = "not_verified: check it"
        with mock.patch.object(self.hook, "_redact_secrets", None):
            code, out = self.run_main(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(out, {})


class TranscriptExtractionTests(unittest.TestCase):
    """Native Antigravity transcript step extraction."""

    def setUp(self):
        self.hook = load_hook()
        self.td = tempfile.TemporaryDirectory(prefix="agy_transcript_test_")
        self.addCleanup(self.td.cleanup)
        self.transcript_file = Path(self.td.name) / "transcript.jsonl"

    def test_last_turn_snippets_extraction(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Please run the tests"},
            {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "I will run them now.", "tool_calls": [{"name": "run_command"}]},
            {"type": "GENERIC", "source": "MODEL", "content": "test output 1"},
            {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "All tests passed successfully."},
        ]
        with self.transcript_file.open("w", encoding="utf-8") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

        snippets = self.hook.last_turn_snippets(str(self.transcript_file))
        self.assertIsNotNone(snippets)
        prompt, reply = snippets
        self.assertEqual(prompt, "Please run the tests")
        self.assertEqual(reply, "All tests passed successfully.")


if __name__ == "__main__":
    unittest.main()
