"""Unit tests for hooks/hermes-stop-review.py (Hermes pre_llm_call cache + pre_verify gate)."""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "hermes-stop-review.py"
GATE_PATH = Path(__file__).resolve().parent.parent / "hooks" / "qoder-stop-audit.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("hermes_stop_review_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _GateStub:
    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []

    def jev_verifier_hint(self, prompt, reply, transcript_path=""):
        self.calls.append((prompt, reply, transcript_path))
        if self.raises is not None:
            raise self.raises
        return self.result


def _pre_llm_payload(session_id="s1", message="phan cong nhieu agent lam viec giup anh",
                     history=None):
    if history is None:
        history = [
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old reply"},
        ]
    return {
        "hook_event_name": "pre_llm_call",
        "session_id": session_id,
        "extra": {"user_message": message, "conversation_history": history},
    }


def _pre_verify_payload(session_id="s1", reply="Done: all tests pass.", attempt=1):
    return {
        "hook_event_name": "pre_verify",
        "session_id": session_id,
        "extra": {"final_response": reply, "attempt": attempt, "coding": True},
    }


class HermesStopReviewTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.tmp = Path(tempfile.mkdtemp(prefix="hermes_stop_review_"))
        for attr in ("STATE_DIR", "LOG_PATH"):
            patcher = mock.patch.object(self.hook, attr,
                                        self.tmp / Path(self.hook.LOG_PATH).name if attr == "LOG_PATH" else self.tmp)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _gate(self, **kwargs):
        stub = _GateStub(**kwargs)
        patcher = mock.patch.object(self.hook, "_load_gate", return_value=stub)
        patcher.start()
        self.addCleanup(patcher.stop)
        return stub

    # --- cache phase (pre_llm_call) ---

    def test_cache_phase_writes_prompt_and_history(self):
        out = self.hook.cache_phase(_pre_llm_payload())
        self.assertEqual(out, {})
        prompt_file, steps_file = self.hook._session_paths("s1")
        self.assertIn("phan cong nhieu agent", prompt_file.read_text(encoding="utf-8"))
        steps = [json.loads(l) for l in steps_file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([s["type"] for s in steps], ["USER_INPUT", "PLANNER_RESPONSE"])

    def test_cache_phase_without_extra_is_noop(self):
        self.assertEqual(self.hook.cache_phase({"session_id": "s1"}), {})
        prompt_file, steps_file = self.hook._session_paths("s1")
        self.assertFalse(prompt_file.exists())
        self.assertFalse(steps_file.exists())

    def test_cache_phase_handles_content_part_lists(self):
        history = [{"role": "user", "content": [{"type": "text", "text": "part one"}]}]
        self.hook.cache_phase(_pre_llm_payload(history=history))
        _, steps_file = self.hook._session_paths("s1")
        steps = [json.loads(l) for l in steps_file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(steps[0]["content"], "part one")

    # --- review phase (pre_verify) ---

    def test_review_blocks_with_steering_reason(self):
        gate = self._gate(result="undone (probability 0.90): required action absent")
        out = self.hook.review_phase(_pre_verify_payload())
        self.assertEqual(out["decision"], "block")
        self.assertIn("undone", out["reason"])
        self.assertEqual(self.hook.steer_count("s1"), 1)
        self.assertEqual(len(gate.calls), 1)

    def test_review_pass_emits_empty_when_gate_silent(self):
        self._gate(result=None)
        self.assertEqual(self.hook.review_phase(_pre_verify_payload()), {})
        self.assertEqual(self.hook.steer_count("s1"), 0)

    def test_review_env_kill_switch_skips_gate(self):
        gate = self._gate(result="should never be consulted")
        with mock.patch.dict(os.environ, {"HERMES_STOP_REVIEW": "0"}):
            self.assertEqual(self.hook.review_phase(_pre_verify_payload()), {})
        self.assertEqual(gate.calls, [])

    def test_review_attempt_beyond_cap_skips(self):
        gate = self._gate(result="blocked")
        out = self.hook.review_phase(_pre_verify_payload(attempt=self.hook.MAX_ATTEMPT + 1))
        self.assertEqual(out, {})
        self.assertEqual(gate.calls, [])

    def test_review_stops_after_session_steer_cap(self):
        self._gate(result="first block")
        self.hook.review_phase(_pre_verify_payload())
        self.hook.review_phase(_pre_verify_payload())
        gate = self._gate(result="third block")
        self.assertEqual(self.hook.review_phase(_pre_verify_payload()), {})
        self.assertEqual(gate.calls, [])
        self.assertEqual(self.hook.steer_count("s1"), 2)

    def test_review_gate_error_fails_open(self):
        self._gate(raises=OSError("network down"))
        self.assertEqual(self.hook.review_phase(_pre_verify_payload()), {})

    def test_review_missing_final_response_is_noop(self):
        gate = self._gate(result="blocked")
        payload = _pre_verify_payload()
        payload["extra"].pop("final_response")
        self.assertEqual(self.hook.review_phase(payload), {})
        self.assertEqual(gate.calls, [])

    def test_review_passes_history_plus_reply_to_gate(self):
        self.hook.cache_phase(_pre_llm_payload())
        gate = self._gate(result=None)
        self.hook.review_phase(_pre_verify_payload(reply="Final claim here."))
        prompt, reply, transcript_path = gate.calls[0]
        self.assertIn("phan cong nhieu agent", prompt)
        self.assertIn("Final claim here.", reply)
        # Review transcript ends with the draft reply as last PLANNER_RESPONSE.
        steps = [json.loads(l) for l in Path(transcript_path).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(steps[-1]["type"], "PLANNER_RESPONSE")
        self.assertEqual(steps[-1]["content"], "Final claim here.")

    def test_review_without_cached_transcript_still_runs_gate(self):
        gate = self._gate(result=None)
        self.hook.review_phase(_pre_verify_payload())
        _, _, transcript_path = gate.calls[0]
        self.assertEqual(transcript_path, "")

    def test_steer_reason_is_clamped(self):
        self._gate(result="x" * 5000)
        out = self.hook.review_phase(_pre_verify_payload())
        self.assertLessEqual(len(out["reason"]), self.hook.STEER_TEXT_LIMIT)

    # --- main() dispatch + shared gate import ---

    def test_main_dispatches_cache_phase(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(_pre_llm_payload()))), \
                redirect_stdout(buf):
            rc = self.hook.main()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), {})
        prompt_file, _ = self.hook._session_paths("s1")
        self.assertTrue(prompt_file.exists())

    def test_main_dispatches_unknown_event_to_noop(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "subagent_stop"}))), \
                redirect_stdout(buf):
            self.hook.main()
        self.assertEqual(json.loads(buf.getvalue()), {})

    def test_main_malformed_stdin_fails_open(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("{not json")), redirect_stdout(buf):
            self.assertEqual(self.hook.main(), 0)
        self.assertEqual(json.loads(buf.getvalue()), {})

    def test_shared_gate_imports_from_qoder_hook(self):
        """The real _load_gate must expose qoder-stop-audit's jev_verifier_hint."""
        self.assertTrue(GATE_PATH.is_file())
        gate = self.hook._load_gate()
        self.assertTrue(callable(getattr(gate, "jev_verifier_hint", None)))


if __name__ == "__main__":
    unittest.main()
