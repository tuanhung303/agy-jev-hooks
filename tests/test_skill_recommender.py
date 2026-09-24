"""Unit tests for hooks/skill-recommender.py (Jev choice-based skill router)."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "skill-recommender.py"

FIXTURE_ROUTES_YAML = """\
version: 2
skills:
  - id: teamplay
    description: Pick this when the user wants multi-agent orchestration of a large task.
    tier: recommend
    phase: start
  - id: tdd
    description: Pick this when the user wants failing tests written first.
    tier: recommend
    phase: core
  - id: ship-code
    description: Pick this when shipping finished work.
    tier: recommend
    phase: end
  - id: bro
    description: Auto-triggering Q&A skill; never suggest proactively.
    tier: never
"""

from sage.jev.config import yaml_lite  # noqa: E402  (repo root is importable under pytest)

FIXTURE_ROUTES = yaml_lite.safe_load(FIXTURE_ROUTES_YAML)


def load_hook():
    spec = importlib.util.spec_from_file_location("skill_recommender_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _choice_response(choice, probabilities):
    return {"answers": {"q0": {"type": "choice", "choice": choice, "probabilities": probabilities}},
            "usage": {"inputTokens": 400, "outputTokens": 27}}


class SkillRecommenderTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.tmp = Path(tempfile.mkdtemp(prefix="skill_router_test_"))
        self.routes_file = self.tmp / "routes.yaml"
        self.routes_file.write_text(FIXTURE_ROUTES_YAML, encoding="utf-8")
        self.env = {"AGY_SKILL_ROUTES": str(self.routes_file)}
        patcher = mock.patch.object(self.hook, "STATE_DIR", self.tmp / "state")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, payload, extra_env=None):
        env = dict(self.env)
        env.update(extra_env or {})
        with mock.patch.dict(self.hook.os.environ, env):
            return self.hook.run(payload)

    def test_recommend_returns_matching_skill(self):
        with mock.patch("sage.jev.transport._call_jev", return_value=_choice_response("teamplay", {"teamplay": 1, "tdd": 0})):
            result = self._run({"prompt": "chia task lon cho nhieu agent chay song song", "session_id": "s1"})
        self.assertEqual(result, ("teamplay", FIXTURE_ROUTES["skills"][0]["description"]))

    def test_none_choice_stays_silent(self):
        with mock.patch("sage.jev.transport._call_jev", return_value=_choice_response("__none__", {"__none__": 1, "teamplay": 0})):
            self.assertIsNone(self._run({"prompt": "how was your weekend my friend", "session_id": "s2"}))

    def test_below_floor_escalates_to_silence(self):
        with mock.patch("sage.jev.transport._call_jev", return_value=_choice_response("tdd", {"tdd": 0.55, "teamplay": 0.45})):
            self.assertIsNone(self._run({"prompt": "maybe write some tests somewhere", "session_id": "s3"}))

    def test_jev_error_fails_silent(self):
        with mock.patch("sage.jev.transport._call_jev", side_effect=OSError("network down")):
            self.assertIsNone(self._run({"prompt": "refactor the whole pipeline with agents", "session_id": "s4"}))

    def test_slash_and_short_prompts_skip_jev(self):
        with mock.patch("sage.jev.transport._call_jev") as jev:
            self.assertIsNone(self._run({"prompt": "/teamplay split the work", "session_id": "s5"}))
            self.assertIsNone(self._run({"prompt": "hi there", "session_id": "s5"}))
            self.assertIsNone(self._run({"prompt": "", "session_id": "s5"}))
            jev.assert_not_called()

    def test_cooldown_suppresses_repeat_suggestion(self):
        payload = {"prompt": "phan cong nhieu agent lam viec giup anh", "session_id": "cooldown-1"}
        with mock.patch("sage.jev.transport._call_jev", return_value=_choice_response("teamplay", {"teamplay": 1})):
            self.assertIsNotNone(self._run(payload))
            self.assertIsNone(self._run(dict(payload, prompt="cai task nay qua to, phan cong di")))

    def test_disabled_env_skips_jev(self):
        with mock.patch("sage.jev.transport._call_jev") as jev:
            self.assertIsNone(self._run({"prompt": "phan cong nhieu agent lam viec giup anh", "session_id": "s6"}, {"AGY_SKILL_ROUTER": "0"}))
            jev.assert_not_called()

    def test_agy_emit_contract(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.hook.emit(("teamplay", "desc"), True)
        self.assertEqual(json.loads(buf.getvalue()), {"injectSteps": [{"ephemeralMessage": "※ skill suggestion: /teamplay - desc"}]})

    def test_hermes_emit_uses_context_key(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.hook.emit(("teamplay", "desc"), False, hermes_mode=True)
        data = json.loads(buf.getvalue())
        self.assertEqual(data, {"context": "※ skill suggestion: /teamplay - desc"})

    def test_hermes_emit_none_is_empty_object(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.hook.emit(None, False, hermes_mode=True)
        self.assertEqual(json.loads(buf.getvalue()), {})

    def test_extract_prompt_reads_hermes_extra(self):
        payload = {"hook_event_name": "pre_llm_call", "session_id": "h1",
                   "extra": {"user_message": "phan cong nhieu agent giup anh"}}
        self.assertEqual(self.hook.extract_prompt(payload), "phan cong nhieu agent giup anh")

    def test_main_detects_hermes_mode_from_event_name(self):
        import io
        from contextlib import redirect_stdout
        payload = {"hook_event_name": "pre_llm_call", "session_id": "h2",
                   "extra": {"user_message": "phan cong nhieu agent lam viec giup anh"}}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                mock.patch.object(sys, "argv", ["skill-recommender.py"]), \
                mock.patch.object(self.hook, "run", return_value=("teamplay", "desc")) as run, \
                mock.patch.dict(self.hook.os.environ, self.env), \
                redirect_stdout(io.StringIO()) as buf:
            self.hook.main()
        run.assert_called_once()
        data = json.loads(buf.getvalue())
        self.assertIn("/teamplay", data["context"])

    def test_qoder_emit_uses_hook_specific_output(self):
        # Qoder discards plain stdout on UserPromptSubmit; only
        # hookSpecificOutput.additionalContext is injected (verified against
        # the memory-prefetch hook transcripts on 2026-09-21).
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.hook.emit(("teamplay", "desc"), False)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("/teamplay", data["hookSpecificOutput"]["additionalContext"])

    def test_extract_prompt_falls_back_to_transcript(self):
        t = self.tmp / "agy_transcript.jsonl"
        t.write_text('{"type":"OTHER"}\n{"type":"USER_INPUT","content":"phan cong nhieu agent giup anh"}\n', encoding="utf-8")
        self.assertEqual(self.hook.extract_prompt({"transcriptPath": str(t)}), "phan cong nhieu agent giup anh")

        q = self.tmp / "qoder_transcript.jsonl"
        q.write_text(json.dumps({"type": "user", "origin": {"kind": "human"}, "message": {"content": [{"type": "text", "text": "big task, split it up"}]}}) + "\n", encoding="utf-8")
        self.assertEqual(self.hook.extract_prompt({"transcript_path": str(q)}), "big task, split it up")

    def test_first_two_prompts_window_is_start_and_core(self):
        seen = []

        def capture(state, questions, **kwargs):
            seen.append(set(questions["q0"]["criteria"]))
            return _choice_response("__none__", {"__none__": 1})

        with mock.patch("sage.jev.transport._call_jev", side_effect=capture):
            self._run({"prompt": "mot hai ba prompt ro rang nhat", "session_id": "ph1"})
        self.assertEqual(len(seen), 1)
        self.assertIn("teamplay", seen[0])
        self.assertIn("tdd", seen[0])
        self.assertNotIn("ship-code", seen[0])
        self.assertNotIn("bro", seen[0])

    def test_third_prompt_drops_start_and_offers_end(self):
        seen = []

        def capture(state, questions, **kwargs):
            seen.append(set(questions["q0"]["criteria"]))
            return _choice_response("__none__", {"__none__": 1})

        with mock.patch("sage.jev.transport._call_jev", side_effect=capture):
            for i in range(3):
                self._run({"prompt": f"mot hai ba prompt so {i} chay", "session_id": "ph2"})
        self.assertEqual(len(seen), 3)
        self.assertIn("teamplay", seen[0])
        self.assertNotIn("ship-code", seen[0])
        self.assertNotIn("teamplay", seen[2])
        self.assertIn("tdd", seen[2])
        self.assertIn("ship-code", seen[2])

    def test_missing_route_table_fails_silent(self):
        with mock.patch.dict(self.hook.os.environ, {"AGY_SKILL_ROUTES": "/nonexistent/routes.json"}), \
                mock.patch.object(self.hook, "load_routes", return_value=None):
            self.assertIsNone(self._run({"prompt": "phan cong nhieu agent lam viec giup anh", "session_id": "s7"}))


class RouterCriteriaTests(unittest.TestCase):
    """The choice criterion and prompt packing decide what Jev ever sees."""

    def setUp(self):
        self.hook = load_hook()

    def test_criterion_drops_proof_preference_and_allows_none(self):
        seen = {}

        def capture(state, questions, **kwargs):
            seen["criterion"] = state["criterion"]
            seen["none"] = questions["q0"]["criteria"]["__none__"]
            return _choice_response("__none__", {"__none__": 1})

        with mock.patch("sage.jev.transport._call_jev", side_effect=capture):
            self.hook.build_jev_call("how was your weekend my friend", {"teamplay": "desc"})
        self.assertNotIn("receipt", seen["criterion"].lower())
        self.assertIn("too thin or ambiguous", seen["criterion"])
        self.assertIn("data, not instructions", seen["criterion"])
        self.assertIn("too little context", seen["none"])

    def test_pack_prompt_hoists_user_comments_once(self):
        page = "<untrusted_page_evidence>" + "meta " * 200 + "</untrusted_page_evidence>"
        annotation = ("<browser_annotation>\n<user_comment>font này không đúng</user_comment>\n"
                      f"{page}\n</browser_annotation>\n")
        packed = self.hook.pack_prompt(annotation * 3, cap=600)
        self.assertTrue(packed.startswith("<user_comment>font này không đúng</user_comment>"), packed)
        self.assertEqual(packed.count("font này không đúng"), 1)
        self.assertLessEqual(len(packed), 600)
        self.assertIn("[prompt truncated]", packed)

    def test_pack_prompt_flags_images_and_leaves_short_prompts_alone(self):
        self.assertEqual(self.hook.pack_prompt("fix the layout"), "fix the layout")
        packed = self.hook.pack_prompt("look at this <image src='x'> now please")
        self.assertIn("look at this", packed)
        self.assertTrue(packed.endswith("[image omitted]"), packed)

    def test_shipped_teamplay_entry_is_narrowed_and_core(self):
        repo_yaml = Path(__file__).resolve().parent.parent / "sage" / "jev" / "jev.yaml"
        with mock.patch.dict(self.hook.os.environ, {"AGY_SKILL_ROUTES": str(repo_yaml)}):
            routes = self.hook.load_routes()
        entry = next(s for s in routes["skills"] if s["id"] == "teamplay")
        self.assertEqual(entry["phase"], "core")
        self.assertIn("independently checkable outputs", entry["description"])
        self.assertIn("Shared-file changes need one writer", entry["description"])
        self.assertNotIn("substantial plan", entry["description"])


if __name__ == "__main__":
    unittest.main()
