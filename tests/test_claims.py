"""Unit tests for the claim to receipt contract (sage.claims)."""
import unittest

from sage import claims


def _call(name, args, cid):
    return {"type": "PLANNER_RESPONSE", "content": "",
            "tool_calls": [{"name": name, "id": cid, "args": args}]}


def _out(cid, content):
    return {"type": "TOOL_OUTPUT", "content": content, "tool_call_id": cid}


class UncoveredClaimTests(unittest.TestCase):
    def test_test_claim_covered_by_passing_run(self):
        steps = [_call("run_command", {"command": "python3 -m pytest tests/ -q"}, "c1"),
                 _out("c1", "exit=0\n12 passed")]
        self.assertEqual(claims.uncovered_claims("Done. Tests pass.", steps), [])

    def test_test_claim_without_receipt_gaps(self):
        steps = [_call("run_command", {"command": "ls"}, "c1"), _out("c1", "exit=0")]
        gaps = claims.uncovered_claims("Done. Tests pass.", steps)
        self.assertEqual(len(gaps), 1)
        self.assertIn("test claim", gaps[0])

    def test_failing_run_does_not_cover(self):
        steps = [_call("run_command", {"command": "pytest -q"}, "c1"),
                 _out("c1", "exit=1\n2 failed")]
        self.assertIn("test claim", claims.uncovered_claims("Tests pass.", steps)[0])

    def test_deploy_claim_needs_url_receipt(self):
        self.assertTrue(claims.uncovered_claims("Deployed to production.", []))
        covered = [_call("run_command", {"command": "curl -s https://app.example.com/health"}, "c1"),
                   _out("c1", "exit=0\n{\"status\":\"ok\"}")]
        self.assertEqual(claims.uncovered_claims("Deployed, is live.", covered), [])

    def test_deploy_claim_covered_by_target_state_check(self):
        """Local deploys have no URL: an ls/stat/hash/diff observation of the
        deployed target is an equally valid receipt."""
        steps = [_call("run_command", {"command": "./scripts/sync.sh"}, "d1"),
                 _out("d1", "Installation complete."),
                 _call("run_command", {"command": "ls -la ~/.config/agy/sage/jev/jev.yaml"}, "d2"),
                 _out("d2", "exit=0\n-rw-r--r-- 20743 Sep 22 17:35 jev.yaml")]
        self.assertEqual(claims.uncovered_claims("Đã deploy hooks xong.", steps), [])

    def test_deploy_script_self_attestation_never_covers(self):
        """The deploy script's own success output is not evidence it landed."""
        steps = [_call("run_command", {"command": "./scripts/sync.sh"}, "d1"),
                 _out("d1", "exit=0\nInstallation complete.")]
        gaps = claims.uncovered_claims("Đã deploy hooks xong.", steps)
        self.assertEqual(len(gaps), 1)
        self.assertIn("deploy claim", gaps[0])

    def test_bare_exit_line_is_not_target_state_evidence(self):
        steps = [_call("run_command", {"command": "ls -la target"}, "d1"),
                 _out("d1", "exit=0")]
        gaps = claims.uncovered_claims("Deployed, is live.", steps)
        self.assertIn("deploy claim", gaps[0])

    def test_echo_self_attestation_never_covers_test_claim(self):
        """echo 'pytest exit=0' tự viết receipt của chính nó, không tính."""
        steps = [_call("run_command", {"command": "echo 'pytest -q: 12 passed exit=0'"}, "c1"),
                 _out("c1", "pytest -q: 12 passed exit=0")]
        gaps = claims.uncovered_claims("Done. Tests pass.", steps)
        self.assertIn("test claim", gaps[0])

    def test_interpreter_one_liner_never_covers_test_claim(self):
        """python -c / node -e cũng tự sinh output như echo."""
        for cmd in ("python3 -c \"print('pytest: 12 passed exit=0')\"",
                    "node -e \"console.log('pytest: 12 passed exit=0')\""):
            steps = [_call("run_command", {"command": cmd}, "c1"),
                     _out("c1", "pytest: 12 passed exit=0")]
            gaps = claims.uncovered_claims("Done. Tests pass.", steps)
            self.assertIn("test claim", gaps[0], cmd)

    def test_echo_self_attestation_never_covers_deploy_claim(self):
        steps = [_call("run_command", {"command": "echo 'https://app.example.com is live'"}, "d1"),
                 _out("d1", "https://app.example.com is live")]
        gaps = claims.uncovered_claims("Đã deploy, is live.", steps)
        self.assertIn("deploy claim", gaps[0])

    def test_prose_word_proof_is_not_a_visual_claim(self):
        """'proof' trong văn bản thuần không phải claim screenshot."""
        self.assertEqual(
            claims.uncovered_claims("Proof must be reproducible: a claim needs receipts.", []),
            [])

    def test_visual_claim_needs_clean_screenshot(self):
        from unittest import mock
        from sage import imgtext
        with mock.patch.object(imgtext, "visual_text_and_flags", return_value=("", [])):
            self.assertIn("no screenshot receipt",
                          claims.uncovered_claims("Fixed. Proof: screenshot attached.", [{}])[0])
        with mock.patch.object(imgtext, "visual_text_and_flags",
                               return_value=("Revenue: NaN", ["NaN"])):
            self.assertIn("screenshot shows NaN",
                          claims.uncovered_claims("Fixed, screenshot proof.", [{}])[0])
        with mock.patch.object(imgtext, "visual_text_and_flags",
                               return_value=("All good", [])):
            self.assertEqual(claims.uncovered_claims("Fixed, screenshot proof.", [{}]), [])

    def test_plain_done_not_flagged(self):
        self.assertEqual(claims.uncovered_claims("Renamed. Done.", []), [])


class HintShapeTests(unittest.TestCase):
    def test_hint_matches_compass_shape(self):
        hint = claims.claim_contract_hint("Done. Tests pass.", [{"type": "USER_INPUT"}])
        self.assertTrue(hint.startswith("jev_compass not_verified:"), hint)
        self.assertIn("Evidence:", hint)

    def test_no_steps_fails_open(self):
        self.assertIsNone(claims.claim_contract_hint("Done. Tests pass.", []))


class NonAssertionTests(unittest.TestCase):
    """Quoted rehearsal, fixtures, and hedged statements are not claims.

    Fixtures are the four adjudicated CLAIM-miss turns of 2026-09-24 whose
    reply carried a claim phrase that was never asserted about this turn.
    """

    def test_reported_fixture_claim_never_fires(self):
        # 4d5822fe t1: the phrase describes a synthetic transcript, not this work.
        reply = ('- FAIL path: synthetic transcript claim "All 22 tests passed, '
                 'deployment verified" → exit 2 với action cụ thể (đòi chạy pytest lấy số thật, '
                 'đối chiếu bundle production).')
        self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_video_script_beat_never_fires(self):
        # 41d09cbe t15: storyboard line with a hold time and node flow.
        reply = ('**5. 3:26-4:25, hold 8s** · "Tests pass" → receipt → sync/retry.py → '
                 'hai node đỏ "no receipt covers it" → review. Đúng hình anh thích, làm cao trào.')
        self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_quoted_narration_negation_never_fires(self):
        # 41d09cbe t15: the narration itself says the phrase may not settle anything.
        reply = ('> "...follow the changed file\'s connections. Two neighboring files have no '
                 'receipts covering them. That does not prove they are broken. It shows why '
                 "'tests pass' may not settle whether the work is finished.\"")
        self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_frame_description_never_fires(self):
        # 41d09cbe t16: animation frames, beats, and a node graph.
        reply = ('- Mắt thường hai frame: beat 5 ra đúng cú pháp graph anh thích, "Tests pass" → '
                 'receipt → `sync/retry.py` xanh → hai node "no receipt covers it" đỏ → review. '
                 'Beat 4 mở từ đúng một node nhỏ "the message".')
        self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_reported_speech_never_fires(self):
        # 41d09cbe t29: "when the agent says ...", a description of the gate.
        reply = ('- Khi agent nói "Tests pass", stop verifier xét completion theo receipts, hard '
                 'finding thì chặn stop và bảo cần xem gì; Jev lỗi thì thả stop (fail-open)')
        self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_negated_and_future_statements_never_fire(self):
        for reply in ("We have not deployed this yet.",
                      "Sẽ deploy sau khi anh duyệt xong.",
                      "Tests pass chưa chắc đủ, cần xem receipt."):
            with self.subTest(reply=reply):
                self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_assertion_claims_skips_rehearsal(self):
        self.assertEqual(claims.assertion_claims('Script beat 3: "tests pass" → receipt'), [])
        self.assertEqual(claims.assertion_claims("443 test pass, đã deploy."),
                         ["443 test pass, đã deploy."])


class RunnerSummaryTests(unittest.TestCase):
    """A pass-count summary counts; a wrapper's exit token never does."""

    def test_piped_suite_summary_covers(self):
        # a648129b t9 / 8a5fd354 t6: `| tail` hides exit=0, the summary stands.
        steps = [_call("run_command",
                       {"command": "python3 -m pytest tests/ -q 2>&1 | tail -4 && uv run ruff check"}, "c1"),
                 _out("c1", "....................... [100%]\n443 passed, 512 subtests passed in 4.46s")]
        self.assertEqual(claims.uncovered_claims("**Chứng cứ** — 443 test pass, lint sạch.", steps), [])

    def test_runner_json_summary_covers(self):
        # 90e27469 t14: npm test and a smoke gate report counts, not exit codes.
        steps = [_call("run_command", {"command": "npm test 2>&1 | tail -8"}, "c1"),
                 _out("c1", "# tests 1345\n# pass 1345\n# fail 0\n# duration_ms 44726"),
                 _call("run_command", {"command": "npm run test:smoke-static 2>&1 | tail -6"}, "c2"),
                 _out("c2", "# pass 37\n# fail 0\n# duration_ms 14159")]
        reply = "Đã kiểm tra trên localhost, typecheck sạch, 1345 test pass, smoke gate 37/37."
        self.assertEqual(claims.uncovered_claims(reply, steps), [])

    def test_wrapper_exit_token_does_not_cover_a_failing_suite(self):
        # `pytest ... | tail` exits 0 while the runner reports failures.
        steps = [_call("run_command", {"command": "python3 -m pytest tests/ -q 2>&1 | tail -3"}, "c1"),
                 _out("c1", "exit=0\nFAILED tests/test_skillpack.py::SkillPackTests\n"
                            "1 failed, 442 passed, 512 subtests passed in 4.26s")]
        gaps = claims.uncovered_claims("443 test pass.", steps)
        self.assertIn("test claim", gaps[0])

    def test_failing_count_beside_passing_count_never_covers(self):
        steps = [_call("run_command", {"command": "npm test"}, "c1"),
                 _out("c1", "# pass 1345\n# fail 2")]
        self.assertIn("test claim", claims.uncovered_claims("1345 test pass.", steps)[0])


class ClaimScopeTests(unittest.TestCase):
    """Receipts must cover the claimed target, not just any run of that kind."""

    def test_scoped_test_receipt_for_another_file_does_not_cover(self):
        steps = [_call("run_command", {"command": "python3 -m pytest tests/test_other.py -q"}, "c1"),
                 _out("c1", "5 passed in 0.04s")]
        gaps = claims.uncovered_claims("Đã sửa `sync/retry.py` xong, tests pass.", steps)
        self.assertIn("test claim", gaps[0])

    def test_matching_test_file_covers(self):
        steps = [_call("run_command", {"command": "python3 -m pytest tests/test_claims.py -q"}, "c1"),
                 _out("c1", "24 passed in 0.08s")]
        self.assertEqual(claims.uncovered_claims("Đã sửa `sage/claims.py`, tests pass.", steps), [])

    def test_whole_suite_run_covers_any_target(self):
        steps = [_call("run_command", {"command": "python3 -m pytest tests/ -q"}, "c1"),
                 _out("c1", "443 passed, 512 subtests passed in 4.26s")]
        self.assertEqual(claims.uncovered_claims("Đã sửa `sage/claims.py`, tests pass.", steps), [])

    def test_deploy_receipt_for_another_host_does_not_cover(self):
        steps = [_call("run_command", {"command": "curl -s https://other.example.com/health"}, "d1"),
                 _out("d1", "{\"status\":\"ok\"}")]
        gaps = claims.uncovered_claims("Deployed to https://staging.example.com", steps)
        self.assertIn("deploy claim", gaps[0])

    def test_deploy_receipt_for_the_claimed_host_covers(self):
        steps = [_call("run_command", {"command": "curl -s https://staging.example.com/health"}, "d1"),
                 _out("d1", "{\"status\":\"ok\"}")]
        self.assertEqual(claims.uncovered_claims("Deployed to https://staging.example.com", steps), [])

    def test_unrelated_url_receipt_does_not_cover_deploy_claim(self):
        # 8a5fd354 t7/t8: a fetched skill URL is not a deploy check.
        steps = [{"type": "PLANNER_RESPONSE", "content": "", "tool_calls": [
                    {"name": "fetch", "id": "f1",
                     "args": {"url": "https://raw.githubusercontent.com/some/repo/main/SKILL.md"}}]},
                 _out("f1", "# Caveman skill\nCompress output.")]
        gaps = claims.uncovered_claims("Đã deploy hooks xong.", steps)
        self.assertIn("deploy claim", gaps[0])

    def test_mere_screenshot_mention_is_not_a_visual_claim(self):
        self.assertEqual(
            claims.uncovered_claims("Em xem screenshot anh gửi, ảnh không có gì sai.", [{}]), [])


if __name__ == "__main__":
    unittest.main()
