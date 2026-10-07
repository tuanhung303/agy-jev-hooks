"""Regression controls for the fifth stop-audit review (sol-review5 findings 1-7)."""
import unittest

from sage import claims

NATIVE_OK = "Created At: now\nCompleted At: now\nThe command exited with code 0.\nOutput:\n"
NATIVE_FAIL = NATIVE_OK.replace("code 0", "code 1")
BACKGROUND = ("Created At: now\nTool is running as a background task with task id: s/task-9\n"
              "Task Description: {cmd}\nTask logs are available at: file:///tmp/x.log\n"
              "YOU MUST TAKE ONE OF THE FOLLOWING TWO ACTIONS")
STATUS_DONE = ("Created At: now\nCompleted At: now\nTask: s/task-9\nStatus: DONE\n"
               "Log: /tmp/x.log\nLog output:\n{log}")
FINISHED = ("<SYSTEM_MESSAGE>\n[Message] sender=s/task-9 content=Task id \"s/task-9\" "
            "finished with result:\n\n{body}\n</SYSTEM_MESSAGE>")


def call(command, cid=None, name="run_command", args=None):
    item = {"name": name, "args": args or {"CommandLine": command}}
    if cid is not None:
        item["id"] = cid
    return {"type": "PLANNER_RESPONSE", "tool_calls": [item]}


def out(body, cid=None, stype="GENERIC"):
    step = {"type": stype, "content": body}
    if cid is not None:
        step["tool_call_id"] = cid
    return step


def pair(command, body, cid=None):
    return [call(command, cid), out(body, cid)]


def background(command, *later):
    status = call("", name="manage_task", args={"Action": "status", "TaskId": "s/task-9"})
    return [call(command), out(BACKGROUND.format(cmd=command)), *[s for step in later for s in (
        [status, step] if step["type"] == "GENERIC" else [step])]]


def gaps(reply, steps):
    return claims.uncovered_claims(reply, steps)


class BackgroundTaskTests(unittest.TestCase):
    """Finding 1: a background launch is rebound to its task's completion record."""

    def test_status_done_summary_covers_test_claim(self):
        steps = background("pytest -q", out(STATUS_DONE.format(log="........ [100%]\n12 passed in 3.2s")))
        self.assertEqual(gaps("Tests pass.", steps), [])

    def test_status_done_dbt_summary_covers_dbt_claim(self):
        steps = background("dbt build", out(STATUS_DONE.format(
            log="Done. PASS=76 WARN=0 ERROR=0 SKIP=0 TOTAL=76")))
        self.assertEqual(gaps("dbt build passed.", steps), [])

    def test_finished_system_message_covers_test_claim(self):
        steps = background("npm test", out(FINISHED.format(
            body="The command exited with code 0.\nOutput:\n# tests 981\n# pass 981\n# fail 0"),
            stype="SYSTEM_MESSAGE"))
        self.assertEqual(gaps("All 981 tests pass.", steps), [])

    def test_unfinished_background_task_abstains(self):
        self.assertEqual(gaps("Tests pass.", background("pytest -q")), [])
        running = out(STATUS_DONE.format(log="....").replace("Status: DONE", "Status: RUNNING"))
        self.assertEqual(gaps("Tests pass.", background("pytest -q", running)), [])

    def test_failed_background_task_still_blocks(self):
        steps = background("pytest -q", out(FINISHED.format(
            body="The command exited with code 1.\nOutput:\n3 failed, 9 passed in 2.1s"),
            stype="SYSTEM_MESSAGE"))
        self.assertIn("test claim", gaps("Tests pass.", steps)[0])
        dbt = background("dbt build", out(STATUS_DONE.format(log="Done. PASS=75 WARN=0 ERROR=1 TOTAL=76")))
        self.assertIn("pipeline/data claim", gaps("dbt build passed.", dbt)[0])


class UnknownRunnerTests(unittest.TestCase):
    """Finding 2: unknown runners and wrappers are unknown, not absent evidence."""

    def test_tsx_runner_and_unittest_script_summaries_cover(self):
        tsx = pair("npx tsx --test src/a.test.ts", NATIVE_OK + "# tests 7\n# pass 7\n# fail 0")
        self.assertEqual(gaps("Tests pass: 7/7.", tsx), [])
        script = pair("python3 scripts/test_builder.py", NATIVE_OK + ".......\n" + "-" * 70
                      + "\nRan 7 tests in 0.1s\n\nOK")
        self.assertEqual(gaps("All unit tests pass.", script), [])

    def test_acceptance_script_abstains_for_compile_and_test_claims(self):
        acc = pair("bash /tmp/example/accept.sh", NATIVE_OK + "== parse\nRunning with dbt=1.11\n"
                   "== compile\nConcurrency: 4 threads\nACCEPT-OK")
        self.assertEqual(gaps("Compile checks passed cleanly.", acc), [])
        self.assertEqual(gaps("All acceptance tests, parse, and compile checks passed cleanly.", acc), [])

    def test_uncaptured_script_abstains_for_test_claim(self):
        self.assertEqual(gaps("All 469 tests passed.", [call("bash scripts/deploy/deploy_dags.sh --clean")]), [])

    def test_dbt_compile_covers_parse_and_compile_claim(self):
        compiled = pair("dbt compile --profiles-dir .", NATIVE_OK + "Found 11 models\nConcurrency: 4 threads")
        self.assertEqual(gaps("Parse and compile checks passed cleanly.", compiled), [])

    def test_controls_still_block(self):
        self.assertIn("test claim", gaps("Tests pass.", pair("true", NATIVE_OK))[0])
        failing = pair("python3 scripts/test_builder.py", NATIVE_FAIL + "Ran 7 tests in 0.1s\n\nFAILED (failures=2)")
        self.assertIn("test claim", gaps("All unit tests pass.", failing)[0])
        unrelated = pair("python3 scripts/render.py", NATIVE_OK + "wrote chart.png")
        self.assertIn("test claim", gaps("All unit tests pass.", unrelated)[0])


class NativeShapeTests(unittest.TestCase):
    """Real-turn shapes found by the round-6 sweep."""

    def test_quoted_native_command_line_is_a_script(self):
        steps = [call('"bash scripts/deploy/deploy_dags.sh --clean"')]
        self.assertEqual(gaps("All 469 tests passed.", steps), [])

    def test_uncaptured_monitor_script_abstains_for_pipeline_claim(self):
        steps = [call('"python3 /tmp/example/monitor_job.py client_a"'),
                 call("", name="schedule", args={"Prompt": "Check DAG status"})]
        self.assertEqual(gaps("The run completed with status SUCCESS.", steps), [])

    def test_uncaptured_py_compile_abstains(self):
        steps = [call('"python3 -m py_compile /tmp/example/a.py /tmp/example/b.py"')]
        self.assertEqual(gaps("Both files compiled successfully.", steps), [])
        ok = pair("python3 -m py_compile /tmp/example/a.py", NATIVE_OK)
        self.assertEqual(gaps("Both files compiled successfully.", ok), [])

    def test_timer_prompt_and_task_status_are_not_pipeline_receipts(self):
        timer = [call("", name="schedule", args={"Prompt": "Check deploy run status"}),
                 out(BACKGROUND.format(cmd="Timer: 20s"))]
        self.assertIn("pipeline/data claim", gaps("Workflow run 34883 completed successfully.", timer)[0])
        status = [call("", name="manage_task", args={"Action": "status", "TaskId": "s/task-9"}),
                  out(STATUS_DONE.format(log="....").replace("Status: DONE", "Status: RUNNING"))]
        self.assertIn("pipeline/data claim", gaps("Workflow run 34883 completed successfully.", status)[0])


class RunIdRecordTests(unittest.TestCase):
    """Finding 3: Azure-style runId records and pipeline names associate."""
    SYNAPSE = ('{"runId":"5f1c-77aa","pipelineName":"Workflow_Example","status":"Succeeded",'
               '"runEnd":"2026-10-07T01:00:00Z"}')
    COMMAND = "az synapse pipeline-run show --workspace-name ws --run-id 5f1c-77aa"

    def test_run_id_name_and_unnamed_claims_are_covered(self):
        steps = pair(self.COMMAND, NATIVE_OK + self.SYNAPSE)
        self.assertEqual(gaps("Pipeline run 5f1c-77aa succeeded.", steps), [])
        self.assertEqual(gaps("Pipeline Workflow_Example completed successfully.", steps), [])
        self.assertEqual(gaps("The pipeline run completed.", steps), [])

    def test_other_run_still_blocks(self):
        steps = pair(self.COMMAND, NATIVE_OK + self.SYNAPSE)
        self.assertIn("pipeline/data claim", gaps("Pipeline run 9b2e-0011 succeeded.", steps)[0])


class NonTerminalStatusTests(unittest.TestCase):
    """Finding 4: a run still in progress contradicts a completion claim."""

    def test_in_progress_blocks_completed_claim(self):
        fabric = pair("curl -s https://example.com/jobs/instances/9a9a",
                      NATIVE_OK + '{"id":"9a9a","status":"InProgress"}')
        self.assertIn("pipeline/data claim", gaps("Pipeline 9a9a completed.", fabric)[0])
        queued = pair("cloud job status r7", NATIVE_OK + '{"run_id":"r7","status":"Queued"}')
        self.assertIn("pipeline/data claim", gaps("Pipeline r7 completed.", queued)[0])

    def test_later_success_read_covers(self):
        steps = (pair("cloud job status r7", NATIVE_OK + '{"run_id":"r7","status":"Running"}')
                 + pair("cloud job status r7", NATIVE_OK + '{"run_id":"r7","status":"Succeeded"}'))
        self.assertEqual(gaps("Pipeline r7 completed.", steps), [])


class NonClaimTextTests(unittest.TestCase):
    """Finding 5: narrative, advice and third-party descriptions are not claims."""
    EDIT = [call("", name="write_to_file", args={"TargetFile": "/tmp/essay.md", "CodeContent": "x"}),
            out(NATIVE_OK)]

    def test_third_party_modal_and_reported_images_are_not_claims(self):
        for reply in ("Once competitors narrowed the gap, Acme deployed Widget commercially.",
                      "The environment must be deployed with `internal = true` for a private IP.",
                      "You can review the published model in two ways:",
                      "Alex gửi link hồ sơ kèm hai ảnh chụp màn hình điện thoại để anh đánh giá.",
                      "Đang gửi lệnh khởi động watcher và kiểm tra ảnh chụp màn hình."):
            with self.subTest(reply=reply):
                self.assertEqual(gaps(reply, self.EDIT), [])

    def test_own_claims_still_count(self):
        self.assertIn("deploy claim", gaps("Deployed the service to staging.", self.EDIT)[0])
        from unittest import mock
        from sage import imgtext
        with mock.patch.object(imgtext, "visual_text_and_flags", return_value=("", [])):
            self.assertIn("visual claim", gaps("Proof: screenshot attached.", self.EDIT)[0])
            self.assertIn("visual claim", gaps("Ảnh chụp đính kèm: result.png.", self.EDIT)[0])


class CountRetryAndRowTests(unittest.TestCase):
    """Findings 6 and 7: failed count queries are skipped; every table row counts."""
    QUERY = 'bq query "select count(*) as source_count, count(*) as target_count from t"'

    def test_failed_query_then_successful_retry_covers(self):
        steps = (pair(self.QUERY.replace("from", "frm"), NATIVE_FAIL + "Error in query string: Syntax error")
                 + pair(self.QUERY, NATIVE_OK + '[{"source_count":120,"target_count":120}]'))
        self.assertEqual(gaps("Row counts match: 120 and 120.", steps), [])

    def test_only_failed_queries_block(self):
        steps = pair(self.QUERY, NATIVE_FAIL + "Error in query string: Syntax error")
        self.assertIn("pipeline/data claim", gaps("Row counts match: 120 and 120.", steps)[0])

    def test_second_table_row_mismatch_blocks(self):
        table = ("name   source_count target_count\n------ ------------ ------------\n"
                 "orders 120          120\nusers  50           49\n\n(2 rows affected)")
        command = 'sqlcmd -Q "select name, count(*) as source_count, count(*) as target_count from x"'
        self.assertIn("pipeline/data claim", gaps("Row counts match.", pair(command, NATIVE_OK + table))[0])
        equal = table.replace("50           49", "50           50")
        self.assertEqual(gaps("Row counts match.", pair(command, NATIVE_OK + equal)), [])


if __name__ == "__main__":
    unittest.main()
