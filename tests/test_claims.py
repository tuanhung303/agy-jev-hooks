"""Unit tests for the claim to receipt contract (sage.claims)."""
import json
import tempfile
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

    def test_published_data_terms_are_not_deploy_claims(self):
        for reply in ("The published measures are selected at run time.",
                      "Daily cohort union across all published attempts.",
                      "Publication status evaluates to published if all expected rules pass."):
            with self.subTest(reply=reply):
                self.assertEqual(claims.uncovered_claims(reply, []), [])

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

    def test_dbt_claim_needs_matching_success_summary(self):
        reply = "dbt build passed."
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, [])[0])
        covered = [_call("run_command", {"command": "dbt build"}, "b1"),
                   _out("b1", "Done. PASS=69 WARN=0 ERROR=0 SKIP=0")]
        self.assertEqual(claims.uncovered_claims(reply, covered), [])

    def test_agy_idless_tool_output_is_a_receipt(self):
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{
                "name": "run_command", "args": {"CommandLine": "dbt build"}}]},
            {"type": "GENERIC", "content": "Created At: now\nDone. PASS=69 WARN=0 ERROR=0"},
        ]
        self.assertEqual(claims.uncovered_claims("dbt build passed.", steps), [])

    def test_native_agy_exit_status_covers_compile_and_build(self):
        compile_ok = [_call("run_command", {"command": "tsc --noEmit"}, "c-native"),
                      _out("c-native", "Created At: now\nThe command exited with code 0.")]
        self.assertEqual(claims.uncovered_claims("Compiled clean.", compile_ok), [])
        build_ok = [_call("run_command", {"command": "npm run build"}, "b-native"),
                    _out("b-native", "Created At: now\nThe command exited with code 0.\nVite built in 234ms")]
        self.assertEqual(claims.uncovered_claims("Build passed.", build_ok), [])
        failed_with_success_text = [
            _call("run_command", {"command": "npm run build"}, "b-fail"),
            _out("b-fail", "The command exited with code 1.\nVite built in 234ms")]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Build passed.", failed_with_success_text)[0])

    def test_native_nonzero_status_overrides_passing_test_summary(self):
        failed = [_call("run_command", {"command": "pytest -q"}, "pytest-native-fail"),
                  _out("pytest-native-fail", "The command exited with code 1.\n12 passed")]
        self.assertIn("test claim",
                      claims.uncovered_claims("Tests pass.", failed)[0])

    def test_dbt_assertion_ignores_fenced_examples_escaped_quotes_and_keeps_inline_command(self):
        # Anonymized f48cc75e-shaped review fixture: source code prints the sentence.
        fenced = '''The reproducer is:
```python
print("Everything is ready. dbt build passed.")
```'''
        self.assertEqual(claims.uncovered_claims(fenced, []), [])
        self.assertEqual(claims.uncovered_claims(
            r'The fixture prints "The code says \"dbt build passed.\""', []), [])
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("`dbt build` passed.", [])[0])

    def test_delayed_native_results_pair_across_planner_records_and_ambiguous_capture_abstains(self):
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "pytest -q"}}]},
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "npm run build"}}]},
            {"type": "GENERIC", "content": "Created At: now\nThe command exited with code 0.\n12 passed"},
            {"type": "GENERIC", "content": "Created At: now\nThe command exited with code 0.\nBuild succeeded"},
        ]
        self.assertEqual(claims.uncovered_claims("Tests pass.", steps), [])
        ambiguous = steps[:-1]
        self.assertEqual(claims.uncovered_claims("Tests pass.", ambiguous), [])
        duplicate_id = [_call("run_command", {"command": "pytest -q"}, "same"),
                        _call("run_command", {"command": "npm test"}, "same"),
                        _out("same", "12 passed\nexit=0")]
        self.assertEqual(claims.uncovered_claims("Tests pass.", duplicate_id), [])

    def test_pipeline_readback_must_match_run_and_terminal_status(self):
        old_run = [_call("run_command", {"command": "cloud job status old-run"}, "p-old"),
                   _out("p-old", '{"id":"old-run","status":"Completed"}')]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Pipeline target-run completed.", old_run)[0])
        failed = [_call("run_command", {"command": "cloud job status"}, "p-failed"),
                  _out("p-failed", '{"id":"r1","status":"Failed",'
                       '"message":"last attempt succeeded"}')]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Pipeline run completed.", failed)[0])
        unrelated_success = [_call("run_command", {"command": "true"}, "v-run"),
                             _out("v-run", "The command exited with code 0.")]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Em đã chạy xong dbt build.", unrelated_success)[0])

    def test_count_reconciliation_requires_equal_labels_or_zero_difference(self):
        mismatched = [_call("run_command", {"command": "select count(*) as source_count, count(*) as target_count"}, "r-bad"),
                      _out("r-bad", "source_count=120 target_count=119")]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Row counts match: 120 and 119.", mismatched)[0])
        repeated_source = [_call("run_command", {"command": "select count(*) as source_count, count(*) as target_count"}, "r-repeat"),
                           _out("r-repeat", "source_count=120\nsource_count=120\ntarget_count=119")]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Row counts match.", repeated_source)[0])
        zero_difference = [_call("run_command", {"command": "select count(*) difference"}, "r-zero"),
                           _out("r-zero", "difference=0")]
        self.assertEqual(claims.uncovered_claims("Row counts match.", zero_difference), [])
        self.assertIn("pipeline/data claim", claims.uncovered_claims(
            "Row counts match: 120 and 119.", zero_difference)[0])

    def test_dbt_summary_normalizes_ansi_and_newlines_and_routes_dbt_test(self):
        ansi_multiline = [_call("run_command", {"command": "dbt build"}, "dbt-ansi"),
                          _out("dbt-ansi", "Done.\x1b[32m PASS=76 WARN=9\nERROR=0\x1b[0m")]
        self.assertEqual(claims.uncovered_claims("dbt build passed.", ansi_multiline), [])
        dbt_test = [_call("run_command", {"command": "dbt test"}, "dbt-test"),
                    _out("dbt-test", "Done. PASS=76 WARN=9 ERROR=0")]
        self.assertEqual(claims.uncovered_claims("dbt test passed.", dbt_test), [])

    def test_auditable_wrapped_api_and_sql_file_receipts_are_supported(self):
        api = [_call("run_command", {"command":
                       "python3 -c \"import requests; print(requests.get('https://example.com/jobs/instances/example-run').json())\""},
                     "api-run"),
               _out("api-run", '{"id":"example-run","status":"Completed"}')]
        self.assertEqual(claims.uncovered_claims("Pipeline run completed.", api), [])
        sql = [_call("read_file", {"path": "reconcile.sql"}, "sql-read"),
               _out("sql-read", "select count(*) as source_count, count(*) as target_count from reconcile_counts"),
               _call("run_command", {"command": "sqlcmd -i reconcile.sql"}, "sql-run"),
               _out("sql-run", "source_count target_count\n120 120")]
        self.assertEqual(claims.uncovered_claims("Row counts match: 120 and 120.", sql), [])

    def test_unrecognized_execution_wrappers_abstain_from_missing_receipt_blocks(self):
        python = [_call("run_command", {"command": "python3 -c \"custom_client.run()\""}, "python-unknown"),
                  _out("python-unknown", '{"id":"r1","status":"Completed"}')]
        self.assertEqual(claims.uncovered_claims("Pipeline run completed.", python), [])
        sql = [_call("run_command", {"command": "sqlcmd -i reconcile.sql"}, "sql-unknown"),
               _out("sql-unknown", "source_count target_count\n120 120")]
        self.assertEqual(claims.uncovered_claims("Row counts match.", sql), [])

    def test_agy_echo_call_does_not_shift_receipt_pairing(self):
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command",
             "args": {"CommandLine": "echo starting"}}]},
            {"type": "GENERIC", "content": "Created At: now\nstarting"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command",
             "args": {"CommandLine": "python -m pytest"}}]},
            {"type": "GENERIC", "content": "Created At: now\n12 passed\nexit=0"},
        ]
        self.assertEqual(claims.uncovered_claims("Tests pass.", steps), [])
        batch = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "echo starting"}},
                {"name": "run_command", "args": {"CommandLine": "python -m pytest"}}]},
            {"type": "GENERIC", "content": "Created At: now\nstarting"},
            {"type": "GENERIC", "content": "Created At: now\n12 passed\nexit=0"},
        ]
        self.assertEqual(claims.uncovered_claims("Tests pass.", batch), [])

    def test_non_dbt_test_and_build_receipts_are_not_pipeline_false_positives(self):
        test_run = [_call("run_command", {"command": "pytest -q"}, "t1"),
                    _out("t1", "12 passed\nexit=0")]
        build_run = [_call("run_command", {"command": "npm run build"}, "b2"),
                     _out("b2", "Build succeeded\nexit=0")]
        self.assertEqual(claims.uncovered_claims("The test run passed.", test_run), [])
        self.assertEqual(claims.uncovered_claims("The build passed.", build_run), [])

    def test_each_pipeline_claim_needs_its_own_kind_of_receipt(self):
        steps = [_call("run_command", {"command": "dbt build"}, "b1"),
                 _out("b1", "Done. PASS=69 WARN=0 ERROR=0")]
        gaps = claims.uncovered_claims("dbt build passed. Row counts match: 120 and 120.", steps)
        self.assertIn("pipeline/data claim", gaps[0])

    def test_pipeline_run_needs_status_readback_with_run_id(self):
        reply = "Pipeline run completed."
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, [])[0])
        no_id = [_call("run_command", {"command": "cloud job status"}, "p1"),
                 _out("p1", "status: Succeeded")]
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, no_id)[0])
        invalid_id = [_call("run_command", {"command": "cloud job status"}, "p-invalid"),
                      _out("p-invalid", "status: Succeeded\nvalid: true")]
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, invalid_id)[0])
        covered = [_call("run_command", {"command": "cloud job status"}, "p2"),
                   _out("p2", "run_id: 123456 status: Succeeded")]
        self.assertEqual(claims.uncovered_claims(reply, covered), [])
        json_status = [_call("run_command", {"command": "cloud job status"}, "p3"),
                       _out("p3", '{"run_id": "123456", "status": "SUCCESS"}')]
        self.assertEqual(claims.uncovered_claims(reply, json_status), [])
        cli_status = [_call("run_command", {"command": "gh run view 123"}, "p4"),
                      _out("p4", "ID: 123\nstatus: completed")]
        self.assertEqual(claims.uncovered_claims(reply, cli_status), [])

    def test_piped_count_rows_cover_data_reconciliation(self):
        steps = [_call("run_command", {"command": "select count(*) from source"}, "s1"),
                 _out("s1", "| 120 |"),
                 _call("run_command", {"command": "select count(*) from target"}, "s2"),
                 _out("s2", "| count |\n| --- |\n| 120 |")]
        self.assertEqual(claims.uncovered_claims("Row counts match.", steps), [])

    def test_compilation_claim_needs_compile_receipt(self):
        reply = "Compiled clean."
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, [])[0])
        covered = [_call("run_command", {"command": "dbt compile"}, "c1"),
                   _out("c1", "Completed successfully\nexit=0")]
        self.assertEqual(claims.uncovered_claims(reply, covered), [])
        tsc = [_call("run_command", {"command": "tsc --noEmit"}, "c2"),
               _out("c2", "exit=0")]
        self.assertEqual(claims.uncovered_claims(reply, tsc), [])

    def test_row_count_claim_needs_matching_count_query_output(self):
        reply = "Row counts match: 120 and 120."
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, [])[0])
        wrong = [_call("run_command", {"command": "select count(*) from source"}, "r1"),
                 _out("r1", "source_count=120 target_count=119")]
        self.assertIn("pipeline/data claim", claims.uncovered_claims(reply, wrong)[0])
        covered = [_call("run_command", {"command": "select count(*) from source and target"}, "r2"),
                   _out("r2", "source_count=120 target_count=120")]
        self.assertEqual(claims.uncovered_claims(reply, covered), [])
        separate = [_call("run_command", {"command": "select count(*) from source"}, "r3"),
                    _out("r3", "source_count=120"),
                    _call("run_command", {"command": "select count(*) from target"}, "r4"),
                    _out("r4", "target_count=120")]
        self.assertEqual(claims.uncovered_claims(reply, separate), [])
        self.assertEqual(claims.uncovered_claims(
            "Row counts match: 120 and 120 on v2.", covered), [])
        metadata_only = [_call("run_command", {"command": "select count(*) from source"}, "r5"),
                         _out("r5", "Created At: 77 77\nsource_count=120 target_count=119")]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Data reconciled.", metadata_only)[0])
        dated_mismatch = [_call("run_command", {"command": "select count(*) from source and target"}, "r6"),
                          _out("r6", "2026-10-06 source_count=120\n2026-10-06 target_count=119")]
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Data reconciled.", dated_mismatch)[0])
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("Data reconciled.", [])[0])

    def test_pipeline_claim_ignores_quoted_negated_and_conditional_text(self):
        for reply in ('The task says "dbt build passed."',
                      "The task says 'dbt build passed.'",
                      '"First sentence. dbt build passed."',
                      'A quoted passage spans lines:\n"First sentence.\ndbt build passed."',
                      "dbt build did not pass.",
                      "If all preliminary staging checks for the pipeline succeeded, let me know.",
                      "If the cloud pipeline completed, then we can publish.",
                      "Tests pass if the deployment finishes before launch."):
            with self.subTest(reply=reply):
                self.assertEqual(claims.uncovered_claims(reply, []), [])

    def test_vietnamese_pipeline_claim_without_receipt(self):
        gaps = claims.uncovered_claims("Build pass, em đã chạy xong.", [])
        self.assertTrue(gaps)
        self.assertIn("pipeline/data claim", gaps[0])

    def test_claim_hint_requires_current_turn_execution_or_edit(self):
        steps = [{"type": "USER_INPUT", "content": "What does SCD2 mean?"}]
        self.assertIsNone(claims.claim_contract_hint(
            "All tests pass and done.", steps, require_action=True))
        executed = steps + [_call("run_command", {"command": "pytest"}, "q1")]
        self.assertIsNotNone(claims.claim_contract_hint(
            "All tests pass and done.", executed, require_action=True))


class HintShapeTests(unittest.TestCase):
    def test_hint_matches_compass_shape(self):
        hint = claims.claim_contract_hint("Done. Tests pass.", [
            {"type": "USER_INPUT"}, _call("run_command", {"command": "pytest"}, "t1")],
            require_action=True)
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

    def test_hook_entry_uses_only_current_turn_tool_calls(self):
        prior = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run tests."},
                 _call("run_command", {"command": "pytest"}, "old"),
                 {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "What does SCD2 mean?"},
                 {"type": "PLANNER_RESPONSE", "content": "All tests pass."}]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as transcript:
            for step in prior:
                transcript.write(json.dumps(step) + "\n")
            transcript.flush()
            self.assertIsNone(claims.claim_contract_hint_for(
                "All tests pass.", transcript.name, require_action=True))


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
