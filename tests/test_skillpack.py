"""Unit tests for the singleton light-skill pack (sage.skillpack)."""
import unittest

from sage import skillpack


class SkillPackTests(unittest.TestCase):
    def test_manifest_loads_with_unique_ids(self):
        pack = skillpack.load_pack()
        for expected in ("tdd", "teamplay", "caveman", "boost", "grill-me"):
            self.assertIn(expected, pack)
        self.assertEqual(len(pack), len(set(pack)))

    def test_every_singleton_file_loads_and_is_bounded(self):
        for skill_id in skillpack.load_pack():
            body = skillpack.skill_markdown(skill_id)
            self.assertTrue(body, f"{skill_id} content missing")
            # Cap is a safety net: a body that only fits after clamping is
            # silently truncated on ingest and must be compressed instead.
            entry = skillpack.load_pack()[skill_id]
            raw = skillpack.FRONTMATTER_RE.sub(
                "", (entry["_dir"] / entry["file"]).read_text(encoding="utf-8")).strip()
            self.assertLessEqual(
                len(raw), skillpack.CONTENT_CHAR_CAP,
                f"{skill_id} exceeds the ingest cap: compress it, do not truncate")

    def test_frontmatter_stripped_from_body(self):
        body = skillpack.skill_markdown("tdd")
        self.assertFalse(body.startswith("---"))
        self.assertIn("TDD Bug Fix", body)

    def test_stop_trigger_matching(self):
        self.assertEqual(skillpack.match_stop_skill("please fix the bug and add a failing test"), "tdd")
        self.assertIsNone(skillpack.match_stop_skill("ship the deck slides"))

    def test_triggers_match_words_not_identifier_fragments(self):
        # A reply discussing the tdd_breach label must not inject the TDD
        # skill; same class: "boost" inside "boosted". Plurals still match.
        self.assertIsNone(skillpack.match_stop_skill("the tdd_breach label fired on this turn"))
        self.assertIsNone(skillpack.match_stop_skill("metrics were boosted this week", phase="prompt"))
        self.assertEqual(skillpack.match_stop_skill("do it with TDD"), "tdd")
        self.assertEqual(skillpack.match_stop_skill("fix the bugs in the parser"), "tdd")

    def test_prompt_only_skill_not_matched_at_stop(self):
        self.assertIsNone(skillpack.match_stop_skill("set up the teamplay session"))

    def test_render_inject_carries_link_and_body(self):
        text = skillpack.render_content_inject("tdd")
        self.assertIn("[@tdd](skill://tdd)", text)
        self.assertIn("fails before the fix", text)

    def test_stop_skill_steer_none_without_trigger(self):
        self.assertIsNone(skillpack.stop_skill_steer("hello", "short reply"))


if __name__ == "__main__":
    unittest.main()
