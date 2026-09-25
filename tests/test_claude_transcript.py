"""Claude Code transcript shapes through the shared sage parser.

Claude writes skill bodies as `isMeta` user records, background-task
notifications as user records with a non-human `origin.kind`, names its tools
`Read`/`Grep`/`Glob`/`Edit`/`Bash`, and flags failed tool calls with
`is_error` on the tool_result block. Each shape must reach the evidence the
way the human turn intended.
"""
import json
import os
import tempfile
import unittest

from sage.jev.evidence.assemble import _read_steps_bounded, assemble_evidence
from sage.jev.request.prompt_pair import extract_prompt_pair
from sage.message_steps import normalize_message_step
from sage.transcript import is_explicit_user_input

PROMPT = "fix the helper in pkg/utils.py and check its callers"


def human(text):
    return {"type": "user", "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def skill_body(text):
    return {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def task_notification(text):
    return {"type": "user", "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": text}}


def tool_use(call_id, name, args):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call_id, "name": name, "input": args}]}}


def tool_result(call_id, text, is_error=False):
    block = {"type": "tool_result", "tool_use_id": call_id, "content": text}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def reply(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": text}]}}


class ClaudeTranscriptCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="claude_transcript_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def steps_of(self, records):
        path = os.path.join(self.root, "transcript.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return _read_steps_bounded(path)


class PromptBoundaryTests(ClaudeTranscriptCase):
    def test_skill_body_is_not_the_user_prompt(self):
        steps = self.steps_of([
            human(PROMPT),
            skill_body("Base directory for this skill: /skills/consult\n\n# Consult"),
            reply("Done."),
        ])
        self.assertEqual(extract_prompt_pair(list(steps))["user"], PROMPT)
        explicit = [s["content"] for s in steps if is_explicit_user_input(s)]
        self.assertEqual(explicit, [PROMPT])

    def test_skill_record_keeps_its_images(self):
        record = skill_body("[Image: original 1920x1080]")
        record["message"]["content"].append(
            {"type": "image", "source": {"type": "base64", "data": "aGVsbG8="}})
        steps = normalize_message_step(record)
        self.assertFalse([s for s in steps if s.get("type") == "USER_INPUT"])
        self.assertEqual([s.get("images") for s in steps], [["aGVsbG8="]])

    def test_task_notification_is_not_the_user_prompt(self):
        steps = self.steps_of([
            human(PROMPT),
            reply("Started a background check."),
            task_notification("<task-notification> <task-id>a1</task-id> done"),
            reply("The check finished."),
        ])
        self.assertEqual(extract_prompt_pair(list(steps))["user"], PROMPT)
        explicit = [s["content"] for s in steps if is_explicit_user_input(s)]
        self.assertEqual(explicit, [PROMPT])


class ToolEvidenceTests(ClaudeTranscriptCase):
    def test_failed_bash_keeps_its_error_status(self):
        steps = self.steps_of([
            human(PROMPT),
            tool_use("t1", "Bash", {"command": "pytest -q"}),
            tool_result("t1", "1 failed, 3 passed", is_error=True),
            reply("One test still fails."),
        ])
        receipts = assemble_evidence(steps)["command_receipts"]
        self.assertIn("$ pytest -q\n[exit=error]", receipts)

    def _workspace(self):
        # Claude runs hooks with cwd at the project root; the blast-radius
        # check scans cwd for callers.
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        pkg = os.path.join(self.root, "pkg")
        os.makedirs(pkg)
        utils = os.path.join(pkg, "utils.py")
        service = os.path.join(pkg, "service.py")
        with open(utils, "w", encoding="utf-8") as handle:
            handle.write("def helper():\n    return 42\n")
        with open(service, "w", encoding="utf-8") as handle:
            handle.write("from pkg.utils import helper\n\ndef run():\n    return helper()\n")
        return utils, service

    def _edit_turn(self, utils, *inspection):
        return self.steps_of([
            human(PROMPT),
            tool_use("e1", "Edit", {"file_path": utils, "old_string": "42", "new_string": "43"}),
            tool_result("e1", "The file has been updated."),
            *inspection,
            reply("Updated the helper."),
        ])

    def test_uninspected_caller_is_flagged(self):
        # Positive control: the blast-radius check sees this workspace at all.
        utils, _service = self._workspace()
        receipts = assemble_evidence(self._edit_turn(utils))["command_receipts"]
        self.assertIn("[blast radius]", receipts)

    def test_claude_read_counts_as_caller_inspection(self):
        utils, service = self._workspace()
        steps = self._edit_turn(
            utils,
            tool_use("r1", "Read", {"file_path": service}),
            tool_result("r1", "from pkg.utils import helper"),
        )
        self.assertNotIn("[blast radius]", assemble_evidence(steps)["command_receipts"])


if __name__ == "__main__":
    unittest.main()
