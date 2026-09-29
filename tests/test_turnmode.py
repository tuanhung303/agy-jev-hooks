"""Unit tests for turn-shape flags (sage.turnmode)."""
import unittest

from sage import turnmode


class TurnModeTests(unittest.TestCase):
    def test_work_orders_flagged_work(self):
        for text in (
            "please fix the bug and add a failing test",
            "chúng ta cần stop this false positive",
            "rename the config flag and run the test suite",
            "nén caveman lại cho ngắn gọn",
        ):
            with self.subTest(text=text):
                self.assertEqual(turnmode.turn_mode(text), turnmode.WORK)

    def test_questions_flagged_normal_qa(self):
        for text in (
            "why is the parser slow on nested groups?",
            "tại sao parser chậm?",
            "what is adstock decay",
            "sao mà để nó undone",
        ):
            with self.subTest(text=text):
                self.assertEqual(turnmode.turn_mode(text), turnmode.NORMAL_QA)

    def test_narrative_flagged_casual_qa(self):
        for text in (
            "hi",
            "the weather is nice today",
            "hay đấy, cảm ơn nhé",
            "ok",
        ):
            with self.subTest(text=text):
                self.assertEqual(turnmode.turn_mode(text), turnmode.CASUAL_QA)

    def test_session_mode_strongest_flag_wins(self):
        thanks = {"type": "USER_INPUT", "content": "thanks"}
        question = {"type": "USER_INPUT", "content": "why is the parser slow?"}
        order = {"type": "USER_INPUT", "content": "fix the bug"}
        self.assertEqual(turnmode.session_mode([thanks, question, order]), turnmode.WORK)
        self.assertEqual(turnmode.session_mode([thanks, question]), turnmode.NORMAL_QA)
        self.assertEqual(turnmode.session_mode([thanks]), turnmode.CASUAL_QA)

    def test_current_mode_judges_the_latest_message(self):
        order = {"type": "USER_INPUT", "content": "fix the bug"}
        for text, mode in (
            ("campaign name và campaign tag khác nhau hả", turnmode.NORMAL_QA),  # closing particle
            ("is the hook still active?", turnmode.NORMAL_QA),
            ("ok làm tiếp đi em", turnmode.WORK),  # a continuation inherits the working session
            ("Do you want it on prod? -> yes, and brainstorm what to try next", turnmode.WORK),
        ):
            with self.subTest(text=text):
                self.assertEqual(turnmode.current_mode([order, {"type": "USER_INPUT", "content": text}]), mode)
        self.assertEqual(turnmode.current_mode([{"type": "USER_INPUT", "content": "thanks"}]),
                         turnmode.CASUAL_QA)


if __name__ == "__main__":
    unittest.main()
