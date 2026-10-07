"""Regression controls for the seventh stop-audit review (sol-review7 Q1 false positives, findings 2, 4, 5, K2)."""
import unittest

from sage import claims

NATIVE_OK = "Created At: now\nCompleted At: now\nThe command exited with code 0.\nOutput:\n"
NATIVE_FAIL = NATIVE_OK.replace("code 0", "code 1")
RUN_ID = "d73b1385-a72b-11f1-bdc9-000d3a086ff6"
POLL_LOG = ("Polling pipeline status for {rid}...\nAttempt 1 status = InProgress\n"
            "Attempt 2 status = {last}\n")
FINISHED = ("<SYSTEM_MESSAGE>\n[Message] timestamp=now sender=s/task-306 priority=MESSAGE_PRIORITY_HIGH "
            "content=Task id \"s/task-306\" finished with result:\n\nThe command exited with code 0.\n"
            "Output:\n{body}\n</SYSTEM_MESSAGE>")
SUBAGENT = ("<SYSTEM_MESSAGE>\n[Message] timestamp=now sender=af7c2535-0bb9-4cf1 priority=MESSAGE_PRIORITY_HIGH "
            "content={body}\n</SYSTEM_MESSAGE>")
TRANSFER = ("=== Syncing dags/min/ -> gs://example-bucket/dags/min (gcloud storage rsync) ===\n"
            "Copying file:///repo/dags/min/pipeline/parser.py to gs://example-bucket/dags/min/pipeline/parser.py\n"
            "=== Sync complete ===")


def call(command, name="run_command", args=None):
    return {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": name, "args": args or {"CommandLine": command}}]}


def out(body, stype="GENERIC"):
    return {"type": stype, "content": body}


def pair(command, body):
    return [call(command), out(body)]


def system(body):
    return {"type": "SYSTEM_MESSAGE", "source": "SYSTEM", "content": body}


def edit(path="/tmp/example/app.py"):
    return [call("", name="replace_file_content", args={"TargetFile": path, "ReplacementContent": "x = 1"}),
            out("Created At: now\nThe following changes were made")]


def gaps(reply, steps, prior=None):
    return claims.uncovered_claims(reply, steps, prior_steps=prior)


class PipelineTestRunTests(unittest.TestCase):
    """b0e9afd2:1: a pipeline "test run" is a pipeline claim, read from its poll log."""

    CLAIM = f"Pipeline `IGA_EXTRACT` test run Succeeded on Synapse (run ID `{RUN_ID}`)."

    def test_restated_run_is_carried_by_the_system_delivered_poll_log(self):
        prior = edit() + [system(FINISHED.format(body=POLL_LOG.format(rid=RUN_ID, last="Succeeded")))]
        self.assertEqual(gaps(self.CLAIM, edit(), prior), [])

    def test_poll_log_in_this_turn_covers(self):
        steps = pair("python3 scripts/poll_run.py", NATIVE_OK + POLL_LOG.format(rid=RUN_ID, last="Succeeded"))
        self.assertEqual(gaps(self.CLAIM, steps), [])

    def test_unfinished_or_missing_poll_still_blocks_as_pipeline_claim(self):
        unfinished = edit() + [system(FINISHED.format(body=POLL_LOG.format(rid=RUN_ID, last="InProgress")))]
        for prior in (unfinished, edit()):
            with self.subTest(prior=len(prior)):
                found = gaps(self.CLAIM, edit(), prior)
                self.assertEqual(len(found), 1)
                self.assertTrue(found[0].startswith("pipeline/data claim"))

    def test_test_runner_sentence_still_goes_to_the_test_check(self):
        self.assertTrue(gaps("The test run passed.", edit())[0].startswith("test claim"))


class TransferLogTests(unittest.TestCase):
    """c4f6f1a0:3, 0fe6788a:2, 201152e9:56: an object-store transfer log observes the deploy."""

    CLAIM = "The fix is deployed to Cloud Composer."

    def test_clean_transfer_log_covers_in_turn_and_carried(self):
        steps = edit() + pair("bash scripts/deploy/deploy_dags.sh", NATIVE_OK + TRANSFER)
        self.assertEqual(gaps(self.CLAIM, steps), [])
        self.assertEqual(gaps(self.CLAIM, pair("git status", NATIVE_OK + "clean"), steps), [])

    def test_unnamed_or_failed_or_url_claims_still_block(self):
        cases = [
            (self.CLAIM, edit() + pair("rsync -av dist/ deploy@web01:/var/www/app/",
                                       NATIVE_OK + "sending incremental file list\nindex.html")),
            (self.CLAIM, edit() + pair("bash scripts/deploy/deploy_dags.sh", NATIVE_FAIL + TRANSFER)),
            ("Deployed to https://app.example.com.", edit() + pair("bash deploy.sh", NATIVE_OK + TRANSFER)),
            ("Deployed other.py to Composer.", edit() + pair("bash deploy.sh", NATIVE_OK + TRANSFER)),
        ]
        for reply, steps in cases:
            with self.subTest(reply=reply):
                self.assertTrue(gaps(reply, steps)[0].startswith("deploy claim"))


class SubagentReportTests(unittest.TestCase):
    """073c6f48:16: a monitor subagent's terminal report makes the run unknown, not missing."""

    CLAIM = "The `min__kleva_brands` DAG run completed successfully on try 5."
    READ = pair("python3 scripts/latest_run.py min__kleva_brands",
                NATIVE_OK + "Latest run for min__kleva_brands is manual__1 (State: failed)")

    def test_subagent_report_abstains(self):
        report = system(SUBAGENT.format(body="SUCCESS: The DAG run for client kleva_brands finished successfully."))
        self.assertEqual(gaps(self.CLAIM, self.READ + [report]), [])

    def test_without_report_or_with_a_task_finish_message_it_blocks(self):
        task_finish = system(FINISHED.format(body="kleva_brands finished successfully"))
        for steps in (self.READ, self.READ + [task_finish]):
            with self.subTest(steps=len(steps)):
                self.assertTrue(gaps(self.CLAIM, steps))


class DbtRunResultsTests(unittest.TestCase):
    """a7dffc0b:0: a read of dbt run_results with zero failures supports the test claim."""

    COMMAND = "python3 -c \"import json; data = json.load(open('target/run_results.json'))\""

    def test_clean_run_results_cover(self):
        body = NATIVE_OK + "Total results: 175\nStatus counts: {'success': 31, 'pass': 143, 'warn': 1}\nFailures: 0"
        self.assertEqual(gaps("All 33 model nodes and tests passed.", pair(self.COMMAND, body)), [])

    def test_failures_or_other_files_block(self):
        bad = NATIVE_OK + "Total results: 175\nStatus counts: {'success': 31, 'fail': 2}\nFailures: 2"
        other = NATIVE_OK + "Status counts: {'success': 31}\nFailures: 0"
        for steps in (pair(self.COMMAND, bad), pair("python3 summarize.py", other)):
            with self.subTest(steps=steps[0]["tool_calls"][0]["args"]["CommandLine"]):
                self.assertTrue(gaps("All 33 model nodes and tests passed.", steps))


class CountParsingTests(unittest.TestCase):
    """Finding 5 and K2: thousands separators, one-sided contradictions, runner counts only."""

    BQ = "bq query --use_legacy_sql=false \"select count(*) as {side}_count from {table}\""

    def test_thousands_separator_is_one_count(self):
        steps = (pair(self.BQ.format(side="source", table="src.orders"),
                      NATIVE_OK + "| source_count |\n|        10452 |")
                 + pair(self.BQ.format(side="target", table="stg.orders"),
                        NATIVE_OK + "| target_count |\n|        10452 |"))
        self.assertEqual(gaps("Row counts match: 10,452 in both source and target.", steps), [])

    def test_one_sided_count_that_contradicts_the_claim_blocks(self):
        steps = pair(self.BQ.format(side="target", table="stg.orders"),
                     NATIVE_OK + "+--------------+\n| target_count |\n+--------------+\n|          119 |\n+--------------+")
        self.assertTrue(gaps("Row counts match: 120 and 120.", steps))

    def test_carried_count_matches_runner_summary_not_any_number(self):
        stamped = NATIVE_OK.replace("Created At: now", "Created At: 2026-10-07T09:12:01+07:00")
        prior = pair("uv run pytest -q", stamped + "." * 30 + "\n30 passed in 1.12s")
        later = pair("git status", NATIVE_OK + "clean")
        self.assertTrue(gaps("All 12 tests pass.", later, prior))
        self.assertEqual(gaps("All 30 tests pass.", later, prior), [])


class TestScriptNameTests(unittest.TestCase):
    """Finding 2: only test or acceptance scripts stand in for a test run."""

    def test_check_and_smoke_scripts_do_not_silence_test_claims(self):
        cases = [
            ("All 48 tests pass.", edit() + pair("bash scripts/check_env.sh", NATIVE_OK + "python ok\nenv ok")),
            ("Unit tests pass: 48 passed.",
             edit() + pair("node scripts/smoke.mjs staging", NATIVE_OK + "/health 200\n2/2 checks ok")),
        ]
        for reply, steps in cases:
            with self.subTest(reply=reply):
                self.assertTrue(gaps(reply, steps))

    def test_test_scripts_abstain_and_all_pass_summaries_cover(self):
        self.assertEqual(gaps("All tests pass.", edit() + pair("bash scripts/run_tests.sh", NATIVE_OK + "ok")), [])
        verify = "node scratch/verify_browser.js"
        passed = NATIVE_OK + "PASS: VIS01\nVerification Summary: 30 / 30 checks passed"
        partial = NATIVE_OK + "FAIL: VIS01\nVerification Summary: 27 / 30 checks passed"
        reply = "Kết quả kiểm thử tự động bằng Playwright đạt 30/30 checks pass."
        self.assertEqual(gaps(reply, edit() + pair(verify, passed)), [])
        self.assertTrue(gaps(reply, edit() + pair(verify, partial)))


class LogOnlyKindTests(unittest.TestCase):
    """Deploy claims log but never block; other kinds still block."""

    def test_verdict_marks_deploy_only_gaps_log_only(self):
        deploy_only = edit() + pair("git push origin main", NATIVE_OK)
        hint, may_block = claims.claim_contract_verdict("Changes deployed to production.", deploy_only)
        self.assertIn("deploy claim", hint)
        self.assertFalse(may_block)
        hint, may_block = claims.claim_contract_verdict(
            "Changes deployed to production. All 12 tests pass.", deploy_only)
        self.assertTrue(may_block)
        self.assertIn("test claim", hint)
        self.assertNotIn("deploy claim", hint)

    def test_plain_hint_keeps_every_kind(self):
        steps = edit() + pair("git push origin main", NATIVE_OK)
        self.assertIn("deploy claim", claims.claim_contract_hint("Changes deployed to production.", steps))


if __name__ == "__main__":
    unittest.main()
