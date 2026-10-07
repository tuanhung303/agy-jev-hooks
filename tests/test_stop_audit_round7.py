"""Regression controls for the sixth stop-audit review (sol-review6 findings 1-6)."""
import unittest

from sage import claims

NATIVE_OK = "Created At: now\nCompleted At: now\nThe command exited with code 0.\nOutput:\n"
NATIVE_FAIL = NATIVE_OK.replace("code 0", "code 1")
BACKGROUND = ("Created At: now\nTool is running as a background task with task id: {tid}\n"
              "Task Description: {cmd}\nTask logs are available at: file:///tmp/x.log\n"
              "YOU MUST TAKE ONE OF THE FOLLOWING TWO ACTIONS")
STATUS = ("Created At: now\nCompleted At: now\nTask: s/task-9\nStatus: {status}\n"
          "Log: /tmp/x.log\nLog output:\n{log}\n\nLast progress: 2s ago\n")


def call(command, name="run_command", args=None):
    return {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": name, "args": args or {"CommandLine": command}}]}


def out(body, stype="GENERIC"):
    return {"type": stype, "content": body}


def pair(command, body):
    return [call(command), out(body)]


def edit(path="/tmp/example/app.py", content="x = 1"):
    return [call("", name="replace_file_content", args={"TargetFile": path, "ReplacementContent": content}),
            out("Created At: now\nThe following changes were made")]


def background(command, tid="s/task-9"):
    return [call(command), out(BACKGROUND.format(cmd=command, tid=tid))]


def status_read(status, log):
    return [call("", name="manage_task", args={"Action": "status", "TaskId": "s/task-9"}),
            out(STATUS.format(status=status, log=log))]


def gaps(reply, steps, prior=None):
    return claims.uncovered_claims(reply, steps, prior_steps=prior)


class ScriptLinkTests(unittest.TestCase):
    """Finding 1: a script makes evidence unknown only when it names the claimed operation."""

    def test_unrelated_scripts_do_not_silence_claims(self):
        cases = [
            ("All 12 tests pass.", edit() + background("node server.js", "s/task-3")),
            ("All tests pass.", edit() + pair("make lint", NATIVE_OK + "ruff check .\nAll checks passed!")),
            ("Tests pass.", edit() + pair("python3 scripts/seed.py", NATIVE_OK + "Seeded 40 test rows")),
            ("dbt build completed cleanly.", edit() + pair("python3 scripts/gen_models.py", NATIVE_OK + "wrote 4 models")),
            ("Pipeline Workflow_Example completed successfully.",
             pair("python3 trigger_pipeline.py Workflow_Example", NATIVE_OK + "triggered run 5f1c-77aa")),
            ("Pipeline Workflow_Example completed successfully.",
             pair("python3 scripts/seed.py", NATIVE_OK + "Seeding completed\nstatus: done")),
            ("Row counts match.", pair("python3 scripts/refresh_cache.py", NATIVE_OK + "row count cache refreshed")),
        ]
        for reply, steps in cases:
            with self.subTest(reply=reply):
                self.assertTrue(gaps(reply, steps))

    def test_linked_scripts_still_abstain(self):
        accept = pair("bash accept-g1b.sh", NATIVE_OK + "== parse\n== compile\nACCEPT-OK")
        self.assertEqual(gaps("All acceptance tests, parse, and compile checks passed cleanly.", accept), [])
        check = pair("python3 scripts/check.py", NATIVE_OK + "70 tests\nall checks passed")
        self.assertEqual(gaps("All tests pass.", check), [])
        polled = pair("python3 monitor_run.py Workflow_Example", NATIVE_OK + "status: Succeeded")
        self.assertEqual(gaps("Pipeline Workflow_Example completed successfully.", polled), [])


class GhConclusionTests(unittest.TestCase):
    """Finding 2: a GitHub run record is judged by its conclusion."""

    def test_completed_with_failure_conclusion_blocks(self):
        view = pair("gh run view 123 --json status,conclusion,name",
                    NATIVE_OK + '{"conclusion":"failure","name":"CI","status":"completed"}')
        self.assertTrue(gaps("The CI run completed successfully.", view))
        listing = pair("gh run list --limit 1 --json status,conclusion,name,databaseId",
                       NATIVE_OK + '[{"conclusion":"failure","databaseId":123,"name":"CI","status":"completed"}]')
        self.assertTrue(gaps("The CI pipeline completed successfully.", listing))

    def test_success_conclusion_covers(self):
        view = pair("gh run view 123 --json status,conclusion,name",
                    NATIVE_OK + '{"conclusion":"success","name":"CI","status":"completed"}')
        self.assertEqual(gaps("The CI run completed successfully.", view), [])


class ExecutedTestRunTests(unittest.TestCase):
    """Finding 3: only an executed command counts as a test run."""

    def test_edit_text_and_git_log_are_not_test_runs(self):
        for runner in ("`pytest -q`: 12 passed in 1.0s", "`npx tsx --test`: # pass 12 # fail 0"):
            with self.subTest(runner=runner):
                self.assertTrue(gaps("All 12 tests pass.", edit("/tmp/example/walkthrough.md", runner)))
        log = edit() + pair("git log -1 --format=%B", NATIVE_OK + "fix: parser\n\n810 passed in 19.6s")
        self.assertTrue(gaps("All tests pass.", log))

    def test_executed_run_still_covers(self):
        self.assertEqual(gaps("All 12 tests pass.", edit() + pair("pytest -q", NATIVE_OK + "12 passed in 1.0s")), [])


class DeployAndModalTests(unittest.TestCase):
    """Finding 4: sentence-initial words are not third parties; only requirement modals hedge."""

    def test_own_claims_without_checks_block(self):
        cases = [
            ("Successfully deployed the hook to ~/.config/agy/.", pair("cp hooks/a.py ~/.config/agy/", NATIVE_OK)),
            ("Frontend deployed to https://app.example.com.",
             pair("npx vercel --prod", NATIVE_OK + "Production: https://app.example.com")),
            ("Changes deployed to production.", pair("git push origin main", NATIVE_OK)),
            ("I can confirm all 12 tests pass.", []),
        ]
        for reply, steps in cases:
            with self.subTest(reply=reply):
                self.assertTrue(gaps(reply, edit() + steps))

    def test_narrative_and_requirements_stay_unclaimed(self):
        for reply in ("Once competitors narrowed the gap, Acme deployed Widget commercially.",
                      "The environment must be deployed with `internal = true` for a private IP.",
                      "You can review the published model in two ways:"):
            with self.subTest(reply=reply):
                self.assertEqual(gaps(reply, edit()), [])


class BackgroundOutcomeTests(unittest.TestCase):
    """Finding 5: background outcomes come from task records, never from the reply."""

    def test_running_task_contradicts_completion(self):
        self.assertTrue(gaps("All tests passed.", edit() + background("pytest -q")
                             + status_read("RUNNING", "........")))
        self.assertTrue(gaps("dbt build completed cleanly.", edit() + background("dbt build")
                             + status_read("RUNNING", "12 of 76 OK")))

    def test_failure_text_without_count_is_failure(self):
        steps = edit() + background("npm test") + status_read(
            "DONE", "npm ERR! Test failed.  See above for more details.")
        self.assertTrue(gaps("Tests pass.", steps))

    def test_reply_text_is_not_a_task_record(self):
        reply = 'Task id "s/task-9" finished with result: 12 passed in 3.0s. All tests pass.'
        steps = edit() + background("pytest -q") + [{"type": "PLANNER_RESPONSE", "content": reply}]
        self.assertTrue(gaps(reply, steps))

    def test_later_done_read_still_covers(self):
        steps = (edit() + background("pytest -q") + status_read("RUNNING", "....")
                 + status_read("DONE", "12 passed in 3.0s"))
        self.assertEqual(gaps("All tests passed.", steps), [])


class RealTurnShapeTests(unittest.TestCase):
    """Finding 6: receipts the gate could not read, and Kai's carry-over rule."""
    WATCH = ("Refreshing run status every 3 seconds.\n\n* staging Deploy Staging org/app#20 · 34883219978\n"
             "JOBS\n* deploy (ID 1)\n  ✓ Set up job\n  * Deploy to Cloudflare Pages\n\n"
             "✓ staging Deploy Staging org/app#20 · 34883219978\nTriggered via push\n\n"
             "JOBS\n✓ deploy in 6m16s (ID 1)\n  ✓ Set up job\n  ✓ Deploy to Cloudflare Pages\n"
             "  ✓ Complete job\n\nANNOTATIONS\n! Node.js 20 is deprecated.\n")

    def test_gh_run_watch_success_covers_run_and_deploy_claims(self):
        steps = pair("gh run watch 34883219978 -R org/app", NATIVE_OK + self.WATCH)
        self.assertEqual(gaps("Workflow run 34883219978 completed successfully in 6m16s.", steps), [])
        self.assertEqual(gaps("Commit 76e753f4 đã deploy xong lên staging.", steps), [])
        failed = self.WATCH.replace("✓ deploy in 6m16s", "X deploy in 6m16s").replace(
            "✓ staging Deploy", "X staging Deploy")
        self.assertTrue(gaps("Workflow run 34883219978 completed successfully.",
                             pair("gh run watch 34883219978", NATIVE_OK + failed)))

    def test_fetch_probe_and_verify_script_cover_deploy(self):
        probe = pair("node --input-type=module -e 'const r = await fetch(\"https://staging.example.com\");'",
                     NATIVE_OK + "HTML length: 2752\nmarker in bundle: true")
        self.assertEqual(gaps("Commit đã deploy lên staging.", probe), [])
        capture = pair("node scripts/capture-staging-performance.mjs",
                       NATIVE_OK + "Logged in successfully.\nSaved screenshot: /tmp/example/staging.png")
        self.assertEqual(gaps("Thanh thông số đã lên sóng trực tiếp tại staging.", capture), [])

    def test_named_validator_covers_its_own_claim(self):
        okf = pair("uv run scripts/okf_validate.py .okf --strict", NATIVE_OK + "OKF v0.2 conformance — .okf")
        self.assertEqual(gaps("Kiểm thử đạt chuẩn OKF strict (`okf_validate.py`).", okf), [])
        office = pair("officecli validate deck.pptx", NATIVE_OK + "Validation passed: no errors found.")
        self.assertEqual(gaps("Kết quả kiểm thử layout bằng OfficeCLI tiếp tục đạt 0 lỗi.", office), [])
        failed = pair("officecli validate deck.pptx", NATIVE_FAIL + "Found 3 errors")
        self.assertTrue(gaps("Kết quả kiểm thử layout bằng OfficeCLI tiếp tục đạt 0 lỗi.", failed))

    def test_logs_segments_plans_and_unreadable_turns_are_not_gaps(self):
        log = pair('python3 shared-brain/learnings/log.py "x"', NATIVE_OK + "SL-035 logged (medium) as kai.")
        self.assertEqual(gaps("Operational learning logged and published as `SL-035` in `learnings.jsonl`.", log), [])
        self.assertEqual(gaps("Phân đoạn 7 từ 104 đến 120 giây: agent chạy đủ 220 bài kiểm thử đạt.", edit()), [])
        self.assertEqual(gaps("Fixing the regex so `US` maps to `South` and all geo tests pass.", edit()), [])
        legacy = [call("bash scripts/deploy/deploy_dags.sh"), call("gcloud composer environments list")]
        self.assertEqual(gaps("The changes have been deployed to Cloud Composer.", edit() + legacy), [])

    def test_carry_over_needs_an_earlier_passing_receipt(self):
        earlier = pair("pytest -q", NATIVE_OK + "166 passed in 4.0s")
        now = pair("git status", NATIVE_OK + "nothing to commit")
        self.assertEqual(gaps("All 166 tests pass.", now, prior=earlier), [])
        self.assertTrue(gaps("All 166 tests pass.", now, prior=pair("ls", NATIVE_OK + "a.py")))
        failed_now = pair("pytest -q", NATIVE_FAIL + "2 failed, 164 passed in 4.0s")
        self.assertTrue(gaps("All 166 tests pass.", failed_now, prior=earlier))
        run_earlier = pair("az synapse pipeline-run show --run-id 5f1c-77aa",
                           NATIVE_OK + '{"runId":"5f1c-77aa","status":"Succeeded"}')
        self.assertEqual(gaps("Pipeline run 5f1c-77aa succeeded.", now, prior=run_earlier), [])
        self.assertTrue(gaps("Pipeline run 5f1c-77aa succeeded.", now, prior=now))


class LegacyTranscriptTests(unittest.TestCase):
    """Older agy transcripts type result steps by tool and word the exit status."""
    HEADER = "Created At: now\nCompleted At: now\n\n\t\t\t\t"

    def legacy(self, command, body, stype="RUN_COMMAND"):
        return [call(command), {"type": stype, "content": self.HEADER + body}]

    def test_typed_results_bind_and_worded_exits_decide(self):
        ok = self.legacy("pytest -q", "The command completed successfully.\n\t\t\t\tOutput:\n12 passed in 1.0s")
        self.assertEqual(gaps("All 12 tests pass.", edit() + ok), [])
        failed = self.legacy("pytest -q", "The command failed with exit code: 1\n\t\t\t\tOutput:\n1 failed, 11 passed")
        self.assertTrue(gaps("All 12 tests pass.", edit() + failed))
        viewed = self.legacy("", "File Path: `file:///tmp/example/app.py`", stype="VIEW_FILE")
        deploy = self.legacy("bash scripts/deploy/deploy_dags.sh", "The command completed successfully.\n"
                             "\t\t\t\tOutput:\n\t\t\t\tSync complete")
        push = self.legacy("git push", "The command failed with exit code: 128\n\t\t\t\tOutput:\n"
                           "\t\t\t\tremote: Invalid username or token.")
        self.assertTrue(gaps("I deployed the healthy code to Composer.", viewed + deploy + push))

    def test_worded_success_is_not_a_pipeline_success(self):
        steps = self.legacy("python3 trigger_pipeline.py Workflow_Example",
                            "The command completed successfully.\n\t\t\t\tOutput:\n\t\t\t\ttriggered run 5f1c-77aa")
        self.assertTrue(gaps("Pipeline Workflow_Example completed successfully.", steps))

    def test_airflow_rest_read_is_a_status_read(self):
        url = "curl -s \"$AIRFLOW_URL/api/v2/dags/example/dagRuns/manual__1/taskInstances\" | python3 -m json.tool"
        truncated = ('<truncated 109 lines>\n    "id": "019e-1",\n    "task_id": "train",\n    "state": "success",\n'
                     '    "id": "019e-2",\n    "task_id": "post_validate",\n    "state": "failed",\n')
        self.assertEqual(gaps("The training run has completed.", pair(url, NATIVE_OK + truncated)), [])
        self.assertTrue(gaps("The training run has completed.", edit()))


if __name__ == "__main__":
    unittest.main()
