#!/usr/bin/env python3
"""Unit tests for the Compass label-driven gate as the sole Stop decision.

Hard label above floor => steer + exit 2; no hard label => clean exit 0.
There is no single-category suspicion fallback (removed: its generic
label+score output was the failure mode this gate replaced).
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "qoder-stop-audit.py"

NEW_FORMAT_STEER = (
    "jev_compass undone: A required deliverable or action is demonstrably absent. "
    "Evidence: run_command exit=0 never ran"
)


def load_hook():
    spec = importlib.util.spec_from_file_location("qoder_stop_audit_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inject_fake_compass(testcase, hint_impl):
    """Stub sage.jev.verdict.compass so the gate resolves it through sys.modules."""
    names = ("sage", "sage.jev", "sage.jev.verdict", "sage.jev.verdict.compass")
    saved = {name: sys.modules.get(name) for name in names}
    sage = types.ModuleType("sage")
    jev_pkg = types.ModuleType("sage.jev")
    verdict_pkg = types.ModuleType("sage.jev.verdict")
    compass_mod = types.ModuleType("sage.jev.verdict.compass")
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(kwargs)
        return hint_impl(*args, **kwargs)

    compass_mod.jev_compass_hint = wrapper
    compass_mod.calls = calls
    sage.jev = jev_pkg
    jev_pkg.verdict = verdict_pkg
    verdict_pkg.compass = compass_mod
    for name, obj in (("sage", sage), ("sage.jev", jev_pkg),
                     ("sage.jev.verdict", verdict_pkg),
                     ("sage.jev.verdict.compass", compass_mod)):
        sys.modules[name] = obj

    def restore():
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    testcase.addCleanup(restore)
    return compass_mod


class QoderJevGateTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        import tempfile
        self._state = tempfile.TemporaryDirectory(prefix="qoder_jev_test_")
        self.addCleanup(self._state.cleanup)
        from pathlib import Path as _Path
        patcher = mock.patch.object(self.hook, "STATE_DIR", _Path(self._state.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hook.LOG_PATH = _Path(self._state.name) / "audit.log"

    def _run_main(self, payload):
        import io
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(json.dumps(payload))), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr):
            return self.hook.main(), stderr.getvalue()

    @staticmethod
    def _payload(session_id):
        return {"session_id": session_id, "transcript_path": "/tmp/x.jsonl"}

    def _patch_snippets(self):
        return mock.patch.object(self.hook, "last_turn_snippets", return_value=("prompt", "reply"))

    def test_hard_label_blocks_stop_with_exit_two(self):
        compass = _inject_fake_compass(self, lambda *a, **k: NEW_FORMAT_STEER)
        with self._patch_snippets():
            code, err = self._run_main(self._payload("s-steer"))
        self.assertEqual(code, 2)
        self.assertEqual(len(compass.calls), 1)
        self.assertIn("[qoder-stop-audit]", err)
        self.assertIn("jev_compass undone:", err)
        self.assertIn("Evidence:", err)

    def test_hard_label_bumps_steer_counter(self):
        _inject_fake_compass(self, lambda *a, **k: NEW_FORMAT_STEER)
        session = "s-counter"
        with self._patch_snippets():
            code, _ = self._run_main(self._payload(session))
        self.assertEqual(code, 2)
        counter = self.hook.STATE_DIR / f"{session}.steers"
        self.assertEqual(counter.read_text(), "1")

    def test_clean_turn_without_hard_label_passes(self):
        _inject_fake_compass(self, lambda *a, **k: None)
        with self._patch_snippets():
            code, err = self._run_main(self._payload("s-clean"))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_no_suspicion_fallback_remains(self):
        """Clean compass must exit 0 with no second-guessing stage behind it."""
        compass = _inject_fake_compass(self, lambda *a, **k: None)
        source = Path(self.hook.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from sage.lite.jev import", source)
        self.assertNotIn("jev_verifier(", source)
        with self._patch_snippets():
            code, err = self._run_main(self._payload("s-fallthrough"))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(len(compass.calls), 1)

    def test_gate_error_fails_open(self):
        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        _inject_fake_compass(self, boom)
        with self._patch_snippets():
            code, err = self._run_main(self._payload("s-error"))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_gate_disabled_by_env(self):
        compass = _inject_fake_compass(self, lambda *a, **k: NEW_FORMAT_STEER)
        with mock.patch.dict(self.hook.os.environ, {"QODER_JEV_GATE": "0"}):
            with self._patch_snippets():
                code, _ = self._run_main(self._payload("s-disabled"))
        self.assertEqual(code, 0)
        self.assertEqual(len(compass.calls), 0)

    def test_claim_contract_survives_jev_kill_switch(self):
        # The kill switch cuts the remote compass call only; the local claim
        # contract keeps steering.
        _inject_fake_compass(self, lambda *a, **k: None)
        claim = mock.patch.object(
            self.hook, "claim_contract_hint_for",
            return_value="jev_compass not_verified: test claim: no test-run receipt with exit=0")
        with claim, self._patch_snippets(), \
                mock.patch.dict(self.hook.os.environ, {"QODER_JEV_GATE": "0"}):
            code, err = self._run_main(self._payload("s-claim-ks"))
        self.assertEqual(code, 2)
        self.assertIn("not_verified", err)

    def test_compass_hint_is_the_steer_text(self):
        _inject_fake_compass(self, lambda *a, **k: NEW_FORMAT_STEER)
        with self._patch_snippets():
            code, err = self._run_main(self._payload("s-compass"))
        self.assertEqual(code, 2)
        self.assertIn("jev_compass undone:", err)

    def test_deadline_is_bounded(self):
        compass = _inject_fake_compass(self, lambda *a, **k: None)
        with self._patch_snippets():
            self._run_main(self._payload("s-budget"))
        self.assertEqual(len(compass.calls), 1)
        deadline = compass.calls[0].get("deadline")
        import time as _time
        self.assertIsNotNone(deadline)
        self.assertLessEqual(deadline - _time.monotonic(),
                             self.hook.JEV_GATE_BUDGET_SECONDS + 0.01)


class CasualRestyleTests(unittest.TestCase):
    """Casual category: robotic or verbose replies get the [@bro](skill://bro) restyle."""

    def setUp(self):
        self.hook = load_hook()
        import tempfile
        self._state = tempfile.TemporaryDirectory(prefix="qoder_casual_test_")
        self.addCleanup(self._state.cleanup)
        patcher = mock.patch.object(self.hook, "STATE_DIR", Path(self._state.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hook.LOG_PATH = Path(self._state.name) / "audit.log"

    def _run(self, reply, compass_hint=None):
        import io
        _inject_fake_compass(self, lambda *a, **k: compass_hint)
        payload = {"session_id": "s-casual", "transcript_path": "/tmp/x.jsonl"}
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(json.dumps(payload))), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr), \
                mock.patch.object(self.hook, "last_turn_snippets", return_value=("prompt", reply)):
            return self.hook.main(), stderr.getvalue()

    def test_casual_import_resolves_sage_candidates(self):
        """Deployed hook must bind the real detector via its candidate paths,
        not silently fall back to the fail-open stub. Reproduces the deployed
        condition: sage importable only through the hook's own resolution."""
        repo_root = HOOK_PATH.parent.parent.resolve()
        saved_path = list(sys.path)
        saved_mods = {n: m for n, m in list(sys.modules.items()) if n == "sage" or n.startswith("sage.")}
        for name in saved_mods:
            del sys.modules[name]
        sys.path[:] = [p for p in sys.path
                       if p and Path(p).resolve() != repo_root and Path(p).resolve() != Path.cwd().resolve()]
        try:
            hook2 = load_hook()
        finally:
            sys.path[:] = saved_path
            for name in [n for n in sys.modules if n == "sage" or n.startswith("sage.")]:
                del sys.modules[name]
            sys.modules.update(saved_mods)
        self.assertEqual(hook2.casual_restyle_hint.__module__, "sage.casual")

    def test_robotic_reply_gets_bro_restyle(self):
        code, err = self._run("Certainly! I hope this helps with your project.")
        self.assertEqual(code, 2)
        self.assertIn("[@bro](skill://bro)", err)
        self.assertIn("[qoder-stop-audit]", err)

    def test_verbose_reply_gets_bro_restyle(self):
        code, err = self._run(" ".join(["plain"] * 250))
        self.assertEqual(code, 2)
        self.assertIn("[@bro](skill://bro)", err)

    def test_hype_pileup_gets_bro_restyle(self):
        code, err = self._run("This is absolutely robust, seamless, and comprehensive.")
        self.assertEqual(code, 2)
        self.assertIn("[@bro](skill://bro)", err)

    def test_evidence_heavy_reply_stays_quiet(self):
        # Evidence records (code, paths, receipts) never get a restyle nudge.
        evidence = "Certainly! I hope this helps.\n" + "\n".join(
            f"sage/mod{i}.py: run($ python3 -m pytest tests/test_{i}.py -q) -> {i}/ok.json"
            for i in range(6))
        code, err = self._run(evidence)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_natural_reply_passes(self):
        code, err = self._run("Fixed the parser. Tests pass now.")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_compass_steer_takes_precedence(self):
        code, err = self._run("Certainly! I hope this helps.", compass_hint=NEW_FORMAT_STEER)
        self.assertEqual(code, 2)
        self.assertIn("jev_compass undone:", err)
        self.assertNotIn("[@bro](skill://bro)", err)


class SkillContentSteerTests(unittest.TestCase):
    """Stop-phase light skills inject their singleton content (e.g. TDD)."""

    def setUp(self):
        self.hook = load_hook()
        import tempfile
        self._state = tempfile.TemporaryDirectory(prefix="qoder_skill_test_")
        self.addCleanup(self._state.cleanup)
        patcher = mock.patch.object(self.hook, "STATE_DIR", Path(self._state.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hook.LOG_PATH = Path(self._state.name) / "audit.log"

    def _run(self, prompt, reply, compass_hint=None):
        import io
        _inject_fake_compass(self, lambda *a, **k: compass_hint)
        payload = {"session_id": "s-skill", "transcript_path": "/tmp/x.jsonl"}
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(json.dumps(payload))), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr), \
                mock.patch.object(self.hook, "last_turn_snippets", return_value=(prompt, reply)):
            return self.hook.main(), stderr.getvalue()

    def test_stop_trigger_injects_skill_content(self):
        code, err = self._run("please fix the bug and add a failing test", "done")
        self.assertEqual(code, 2)
        self.assertIn("[@tdd](skill://tdd)", err)
        self.assertIn("fails before the fix", err)
        self.assertIn("[qoder-stop-audit]", err)

    def test_no_trigger_stays_quiet(self):
        code, err = self._run("hello there", "Fixed the parser. Tests pass now.")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_compass_steer_wins_over_skill_content(self):
        code, err = self._run("fix the bug failing test", "done", compass_hint=NEW_FORMAT_STEER)
        self.assertEqual(code, 2)
        self.assertIn("jev_compass undone:", err)
        self.assertNotIn("[@tdd](skill://tdd)", err)


class ClaimContractHookTests(unittest.TestCase):
    """Layer 3 at the hook: claim without matching receipt is not_verified."""

    def setUp(self):
        self.hook = load_hook()
        import tempfile
        self._state = tempfile.TemporaryDirectory(prefix="qoder_claim_test_")
        self.addCleanup(self._state.cleanup)
        patcher = mock.patch.object(self.hook, "STATE_DIR", Path(self._state.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hook.LOG_PATH = Path(self._state.name) / "audit.log"

    def _run(self, reply, with_receipt):
        import io
        entries = [
            {"type": "user", "origin": {"kind": "human"},
             "message": {"role": "user", "content": [{"type": "text", "text": "fix the parser and run tests"}]}},
        ]
        if with_receipt:
            entries.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "python3 -m pytest tests/ -q"}}]}})
            entries.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "exit=0\n12 passed"}]}})
        entries.append({"type": "assistant",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
        transcript = Path(self._state.name) / "t.jsonl"
        transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
        _inject_fake_compass(self, lambda *a, **k: None)
        payload = {"session_id": "s-claim", "transcript_path": str(transcript)}
        stderr = io.StringIO()
        with mock.patch.object(self.hook.sys, "stdin", new=io.StringIO(json.dumps(payload))), \
                mock.patch.object(self.hook.sys, "stderr", new=stderr), \
                mock.patch.object(self.hook, "last_turn_snippets", return_value=("prompt", reply)):
            return self.hook.main(), stderr.getvalue()

    def test_claim_without_receipt_gets_not_verified(self):
        code, err = self._run("Done. Tests pass.", with_receipt=False)
        self.assertEqual(code, 2)
        self.assertIn("not_verified", err)
        self.assertIn("test claim", err)

    def test_claim_with_passing_receipt_passes(self):
        code, err = self._run("Done. Tests pass.", with_receipt=True)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
