"""Unit tests for screenshot OCR and symptom flags (sage.imgtext)."""
import os
import tempfile
import unittest
from unittest import mock

from sage import imgtext


class SymptomFlagTests(unittest.TestCase):
    def test_known_failure_strings_flagged(self):
        blob = "Revenue: NaN\nUser card: [object Object]\nSomething went wrong\nLoading...\n429 Too Many Requests\nSign in"
        flags = imgtext.symptom_flags(blob)
        for expected in ("NaN", "[object Object]", "error banner", "unrendered/loading", "rate limited", "auth screen"):
            self.assertIn(expected, flags)

    def test_clean_text_unflagged(self):
        self.assertEqual(imgtext.symptom_flags("Revenue: 1200\nUsers: 34\nAll systems normal"), [])


class OcrTests(unittest.TestCase):
    def test_missing_file_fails_open(self):
        self.assertEqual(imgtext.ocr_image("/nonexistent/nope.png"), "")

    def test_engine_output_returned_capped(self):
        with mock.patch.object(imgtext.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="NaN text", stderr="")
            self.assertEqual(imgtext.ocr_image(__file__), "NaN text")

    def test_engine_failure_fails_open(self):
        with mock.patch.object(imgtext.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            self.assertEqual(imgtext.ocr_image(__file__), "")


class FindImageTests(unittest.TestCase):
    def test_paths_from_tool_args_and_text(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as img:
            steps = [
                {"type": "PLANNER_RESPONSE", "content": f"proof: {img.name}",
                 "tool_calls": [{"name": "run_command", "args": {"command": "open /missing/gone.png"}}]},
            ]
            self.assertEqual(imgtext.find_image_paths(steps), [img.name])

    def test_capped_and_deduped(self):
        paths = []
        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                p = os.path.join(d, f"img{i}.png")
                open(p, "wb").close()
                paths.append(p)
            steps = [{"type": "USER_INPUT", "content": " ".join(paths + [paths[0]])}]
            found = imgtext.find_image_paths(steps)
            self.assertLessEqual(len(found), imgtext.MAX_IMAGES)
            self.assertEqual(len(found), len(set(found)))


class EmbeddedImageTests(unittest.TestCase):
    def test_message_records_expose_embedded_images(self):
        from sage import message_steps
        msg = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "image", "source": {"type": "base64", "data": "aGVsbG8="}},
            {"type": "text", "text": "done"}]}}
        steps = message_steps.normalize_message_step(msg)
        self.assertEqual(steps[0].get("images"), ["aGVsbG8="])

    def test_embedded_images_reach_ocr_and_cleanup(self):
        seen = []

        def fake_ocr(path):
            seen.append(path)
            return "NaN"

        steps = [{"type": "PLANNER_RESPONSE", "content": "", "images": ["aGVsbG8="]}]
        with mock.patch.object(imgtext, "ocr_image", side_effect=fake_ocr):
            text, flags = imgtext.visual_text_and_flags(steps)
        self.assertEqual(len(seen), 1)
        self.assertFalse(os.path.exists(seen[0]), "temp image must be cleaned up")
        self.assertIn("NaN", text)
        self.assertIn("NaN", flags)

    def test_image_only_message_keeps_attachment(self):
        from sage import message_steps
        msg = {"type": "user", "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "aGVsbG8="}}]}}
        steps = message_steps.normalize_message_step(msg)
        self.assertEqual(steps[0]["type"], "CHECKPOINT")
        self.assertEqual(steps[0]["images"], ["aGVsbG8="])


if __name__ == "__main__":
    unittest.main()
