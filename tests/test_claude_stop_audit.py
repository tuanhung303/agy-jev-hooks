#!/usr/bin/env python3
"""Contract tests for hooks/claude-stop-audit.py (Claude Code Stop).

Covers shadow versus block mode, the edit-turn scope of the claim contract,
per-gate caps, the payload guards and kill switches, and fail-open Compass
errors. Compass is stubbed; the claim contract runs for real on synthetic
Claude transcripts.
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sage.guards import is_steering_message
from test_claude_transcript import human, reply, tool_result, tool_use

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "claude-stop-audit.py"
SESSION = "22222222-2222-4222-8222-222222222222"
CLAIM_REPLY = "Deployed the fix to https://app.example.com and it is live."
COMPASS_STEER = "jev_compass undone: A requested deliverable is missing. Evidence: no file written."


def load_hook():
    spec = importlib.util.spec_from_file_location("claude_stop_audit_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClaudeStopAuditTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        tmp = tempfile.TemporaryDirectory(prefix="claude_stop_audit_")
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
        for key in ("CLAUDE_STOP_AUDIT", "CLAUDE_STOP_AUDIT_MODE", "CLAUDE_JEV_GATE"):
            os.environ.pop(key, None)
        self.real_compass_hint = self.hook.compass_hint
        compass = mock.patch.object(self.hook, "compass_hint", return_value=None)
        self.compass = compass.start()
        self.addCleanup(compass.stop)

    def transcript(self, *records):
        path = self.root / "transcript.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return str(path)

    def edit_turn(self, final=CLAIM_REPLY):
        return self.transcript(
            human("ship the fix for the login redirect"),
            tool_use("e1", "Edit", {"file_path": "/repo/app/login.py",
                                    "old_string": "a", "new_string": "b"}),
            tool_result("e1", "The file has been updated."),
            reply(final),
        )

    def review_turn(self):
        return self.transcript(
            human("review this release plan"),
            tool_use("r1", "Read", {"file_path": "/repo/PLAN.md"}),
            tool_result("r1", "step 1: deploy"),
            reply(CLAIM_REPLY),
        )

    def run_main(self, transcript_path, **payload):
        body = {"session_id": SESSION, "transcript_path": transcript_path,
                "cwd": "/repo", "hook_event_name": "Stop", "stop_hook_active": False}
        body.update(payload)
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(json.dumps(body))), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr):
            code = self.hook.main()
        return code, stderr.getvalue()

    def records(self):
        try:
            text = self.hook.LOG_PATH.read_text(encoding="utf-8")
        except OSError:
            return []
        return [json.loads(line) for line in text.splitlines()]

    def test_shadow_logs_the_claim_verdict_and_passes(self):
        code, stderr = self.run_main(self.edit_turn())
        self.assertEqual((code, stderr), (0, ""))
        record = self.records()[-1]
        self.assertEqual(record["mode"], "shadow")
        self.assertEqual(record["decision"], "would_block:claim")
        self.assertEqual(record["writes"], 1)
        self.assertIn("deploy claim", record["claim"])
        self.assertEqual(record["prompt"], "ship the fix for the login redirect")
        self.compass.assert_called_once()

    def test_block_mode_steers_then_caps_the_gate(self):
        os.environ["CLAUDE_STOP_AUDIT_MODE"] = "block"
        path = self.edit_turn()
        results = [self.run_main(path) for _ in range(self.hook.MAX_STEERS_PER_GATE + 1)]
        for code, stderr in results[:-1]:
            self.assertEqual(code, 2)
            self.assertTrue(stderr.startswith("[claude-stop-audit] jev_compass not_verified"))
        self.assertEqual(results[-1], (0, ""))
        decisions = [r["decision"] for r in self.records()]
        self.assertEqual(decisions, ["block:claim", "block:claim", "capped:claim"])

    def test_review_turn_skips_the_claim_contract(self):
        code, _ = self.run_main(self.review_turn())
        record = self.records()[-1]
        self.assertEqual(code, 0)
        self.assertEqual(record["writes"], 0)
        self.assertIsNone(record["claim"])
        self.assertEqual(record["decision"], "pass")

    def test_block_mode_steers_on_a_compass_label(self):
        os.environ["CLAUDE_STOP_AUDIT_MODE"] = "block"
        self.compass.return_value = COMPASS_STEER
        code, stderr = self.run_main(self.review_turn())
        self.assertEqual(code, 2)
        self.assertEqual(stderr, f"[claude-stop-audit] {COMPASS_STEER}\n")
        self.assertEqual(self.records()[-1]["decision"], "block:compass")

    def test_payload_reply_wins_over_an_unflushed_transcript(self):
        path = self.edit_turn(final="Working on it.")
        self.run_main(path, last_assistant_message=CLAIM_REPLY)
        record = self.records()[-1]
        self.assertFalse(record["reply_in_transcript"])
        self.assertIn("deploy claim", record["claim"])

    def test_continuation_of_a_blocked_stop_is_not_audited(self):
        code, _ = self.run_main(self.edit_turn(), stop_hook_active=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.records(), [])
        self.compass.assert_not_called()

    def test_kill_switch_and_missing_transcript_fail_open(self):
        os.environ["CLAUDE_STOP_AUDIT"] = "0"
        self.assertEqual(self.run_main(self.edit_turn()), (0, ""))
        os.environ.pop("CLAUDE_STOP_AUDIT")
        self.assertEqual(self.run_main(str(self.root / "missing.jsonl")), (0, ""))
        self.assertEqual(self.records(), [])

    def test_compass_kill_switch_and_errors_fail_open(self):
        with mock.patch("sage.jev.verdict.compass.jev_compass_hint",
                        side_effect=RuntimeError("gateway down")) as gate:
            self.assertIsNone(self.real_compass_hint("/nowhere.jsonl"))
            self.assertEqual(self.records()[-1]["error"], "compass unavailable: RuntimeError")
            os.environ["CLAUDE_JEV_GATE"] = "0"
            gate.reset_mock()
            self.assertIsNone(self.real_compass_hint("/nowhere.jsonl"))
            gate.assert_not_called()

    def test_fed_back_steer_is_not_a_user_prompt(self):
        self.assertTrue(is_steering_message(f"Stop hook feedback:\n[claude-stop-audit] {COMPASS_STEER}"))


if __name__ == "__main__":
    unittest.main()
