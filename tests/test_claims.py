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


if __name__ == "__main__":
    unittest.main()
