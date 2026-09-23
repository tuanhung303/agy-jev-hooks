"""Scaffold tests: case parser, jev.yaml routing source of truth, prompt pair."""
import unittest

from sage.jev.request import prompt_pair
from sage.jev.config.catalog import COMPASS_CATEGORIES, ROUTE_FLOORS, load_routing
from sage.jev.request.parser import (
    build_request,
    load_case,
    parse_boolean_answers,
    parse_choice_answer,
    parse_probability,
)


class CatalogTests(unittest.TestCase):
    def test_yaml_lite_matches_pyyaml_on_real_configs(self):
        """The hermetic loader must be byte-equivalent to PyYAML on our files.

        PyYAML is absent in hook subprocesses (HOME isolation drops user
        site), so this oracle test is the safety net for subset drift.
        """
        try:
            import yaml  # PyYAML: oracle only, never runtime
        except ImportError:  # pragma: no cover - CI with PyYAML always runs this
            self.skipTest("PyYAML oracle unavailable")
        from sage.jev.config import yaml_lite
        from sage.jev.config.catalog import ROUTING_PATH
        from sage.jev.request.parser import CASES_PATH
        for path in (ROUTING_PATH, CASES_PATH):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(yaml_lite.safe_load(text), yaml.safe_load(text), path.name)

    def test_yaml_lite_edge_cases(self):
        from sage.jev.config import yaml_lite
        doc = (
            "top: { a: 1, b: two }\n"
            "seq: [x, y, z]\n"
            "quoted: 'it''s: fine # not a comment'\n"
            'escaped: "line\\nbreak"\n'
            "flag: true\n"
            "missing: null\n"
            "num: -2.5\n"
            "folded: >-\n"
            "  one two\n"
            "  three\n"
            "literal: |\n"
            "  keep\n"
            "  lines\n"
            "list:\n"
            "  - plain\n"
            "  - id: inline\n"
            "    key: value\n"
            "# trailing comment\n"
        )
        out = yaml_lite.safe_load(doc)
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("PyYAML oracle unavailable")
        self.assertEqual(out, yaml.safe_load(doc))

    def test_yaml_lite_rejects_unsupported(self):
        from sage.jev.config import yaml_lite
        with self.assertRaises(ValueError):
            yaml_lite.safe_load("key: 'unterminated\n")
        with self.assertRaises(ValueError):
            yaml_lite.safe_load("outer:\n  - bad indent item\n   deeper: x\n")

    def test_catalog_categories_and_floors(self):
        self.assertEqual(len(COMPASS_CATEGORIES), 23)
        self.assertEqual(ROUTE_FLOORS, {"H": 0.75, "N": 0.7, "A": 0.6})
        routes = [spec["route"] for spec in COMPASS_CATEGORIES.values()]
        self.assertEqual(routes.count("H"), 18)
        self.assertEqual(routes.count("N"), 4)
        self.assertEqual(routes.count("A"), 1)

    def test_every_pair_has_criterion_and_narrative(self):
        slots = ("{E}", "{R}", "{T}", "{C}", "{K}", "{D}")
        for name, spec in COMPASS_CATEGORIES.items():
            self.assertTrue(spec["criterion"], name)
            self.assertTrue(any(slot in spec["narrative"] for slot in slots), name)

    def test_all_seven_legacy_suspicion_categories_survive(self):
        legacy = {"constraint_breach", "incorrect", "claim_conflict", "premature_stop",
                  "undone", "not_verified", "needs_simplification"}
        self.assertLessEqual(legacy, set(COMPASS_CATEGORIES))

    def test_fixture_pairs_present(self):
        routing = load_routing()
        pairs = routing.get("pairs") or []
        self.assertGreaterEqual(len(pairs), 3)
        for pair in pairs:
            self.assertIn(pair["expected"], ("pass", "failed"))
            self.assertIn("user_prompt", pair)
            self.assertIn("agent_reply", pair)


class ParserTests(unittest.TestCase):
    def test_compass_case_expands_q_pass_plus_categories(self):
        body = build_request("compass", {"evidence": "EV", "last_user": "u", "last_agent": "a"})
        self.assertEqual(len(body["questions"]), 24)
        self.assertIn("q_pass", body["questions"])
        self.assertEqual(body["questions"]["undone"]["type"], "boolean")
        self.assertIn("EV", body["state"]["criterion"])

    def test_missing_placeholder_raises(self):
        with self.assertRaises(ValueError):
            build_request("compass", {"evidence": "EV"})  # pair placeholders missing

    def test_router_choice_criteria_from_context(self):
        body = build_request("router", {
            "user_prompt": "do the thing",
            "agents_md": "ctx",
            "rules_head": "rules",
            "criteria": {"thing-skill": "does the thing"},
        })
        self.assertEqual(body["questions"]["q0"]["criteria"]["thing-skill"], "does the thing")

    def test_router_choice_falls_back_to_default(self):
        body = build_request("router", {
            "user_prompt": "hi", "agents_md": "", "rules_head": "",
        })
        self.assertIn("__none__", body["questions"]["q0"]["criteria"])

    def test_verifier_case(self):
        body = build_request("verifier", {
            "reasons": "undone p=0.76",
            "candidate_count": 1,
            "candidates": "s1: never ran tests",
            "criteria": {"s1": "never ran tests"},
        })
        self.assertEqual(body["questions"]["q0"]["type"], "choice")
        self.assertEqual(body["questions"]["q0"]["criteria"]["s1"], "never ran tests")

    def test_unknown_case_rejected(self):
        with self.assertRaises(ValueError):
            load_case("nope")

    def test_case_missing_section_rejected(self):
        from sage.jev.request import parser
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp, "jev.yaml")
            bad.write_text("version: 1\ncompass:\n  state: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parser.load_case("compass", bad)

    def test_probability_rejects_bool_and_nan(self):
        self.assertIsNone(parse_probability(True))
        self.assertIsNone(parse_probability(float("nan")))
        self.assertIsNone(parse_probability(1.5))
        self.assertEqual(parse_probability(0.7), 0.7)


class PromptPairTests(unittest.TestCase):
    def test_last_human_pair_skips_steering(self):
        steps = [
            {"type": "USER_INPUT", "content": "ship the parser"},
            {"type": "PLANNER_RESPONSE", "content": "done"},
            {"type": "USER_INPUT", "content": "[qoder-stop-audit] undone p=0.76"},
            {"type": "USER_INPUT", "content": "※ skill suggestion: /tdd"},
            {"type": "USER_INPUT", "content": "now add retries"},
            {"type": "PLANNER_RESPONSE", "content": "retries added"},
        ]
        pair = prompt_pair.extract_prompt_pair(steps)
        self.assertEqual(pair["user"], "now add retries")
        self.assertEqual(pair["agent"], "retries added")

    def test_non_human_origin_excluded(self):
        steps = [
            {"type": "user", "content": "real ask", "origin": {"kind": "human"}},
            {"type": "user", "content": "injected context", "origin": {"kind": "hook"}},
        ]
        pair = prompt_pair.extract_prompt_pair(steps)
        self.assertEqual(pair["user"], "real ask")

    def test_bound_applied(self):
        steps = [{"type": "USER_INPUT", "content": "x" * 9000},
                 {"type": "PLANNER_RESPONSE", "content": "y" * 9000}]
        pair = prompt_pair.extract_prompt_pair(steps)
        self.assertEqual(len(pair["user"]), 4000)
        self.assertEqual(len(pair["agent"]), 4000)


if __name__ == "__main__":
    unittest.main()
