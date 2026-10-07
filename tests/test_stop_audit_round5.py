"""Regression controls for the fourth stop-audit review (sol-review4 R1-R8)."""
import unittest

from sage import claims
from sage.claim_receipts import _call_output_pairs
from sage.jev.evidence.assemble import assemble_evidence
from sage.jev.verdict.support import label_support

NATIVE_OK = "Created At: now\nCompleted At: now\nThe command exited with code 0.\nOutput:\n"
NATIVE_FAIL = NATIVE_OK.replace("code 0", "code 1")


def call(command, cid=None, name="run_command", args=None):
    item = {"name": name, "args": args or {"CommandLine": command}}
    if cid is not None:
        item["id"] = cid
    return {"type": "PLANNER_RESPONSE", "tool_calls": [item]}


def out(body, cid=None):
    step = {"type": "GENERIC", "content": body}
    if cid is not None:
        step["tool_call_id"] = cid
    return step


def pair(command, body, cid=None):
    return [call(command, cid), out(body, cid)]


def fail_support(steps, reply):
    full = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Do the task."}] + steps + [
        {"type": "PLANNER_RESPONSE", "content": reply}]
    return label_support("claim_conflict", assemble_evidence(full))


class ExplicitResultBindingTests(unittest.TestCase):
    """R1: explicit results are never positional candidates."""

    def test_idless_success_is_not_replaced_by_explicit_failure(self):
        steps = [call("false", "a"), call("pytest -q"),
                 out(NATIVE_FAIL + "1 failed", "a"), out(NATIVE_OK + "12 passed")]
        self.assertEqual(claims.uncovered_claims("Tests pass.", steps), [])
        pytest_pair = next(p for p in _call_output_pairs(steps)[0] if "pytest" in str(p[0]["args"]))
        self.assertIn("12 passed", pytest_pair[1])

    def test_idless_failure_is_not_hidden_by_explicit_success(self):
        steps = [call("true", "a"), call("pytest -q"),
                 out(NATIVE_OK, "a"), out(NATIVE_FAIL + "1 failed")]
        self.assertIn("test claim", claims.uncovered_claims("Tests pass.", steps)[0])

    def test_idless_dbt_summary_after_explicit_pytest_result(self):
        steps = [call("pytest -q", "p"), call("dbt build"),
                 out(NATIVE_OK + "12 passed", "p"), out(NATIVE_OK + "Done. PASS=76 WARN=9 ERROR=0")]
        self.assertEqual(claims.uncovered_claims("dbt build passed.", steps), [])

    def test_fully_tagged_control_passes(self):
        steps = pair("false", NATIVE_FAIL + "1 failed", "a") + pair("pytest -q", NATIVE_OK + "12 passed", "p")
        self.assertEqual(claims.uncovered_claims("Tests pass.", steps), [])

    def test_result_is_consumed_once(self):
        steps = [call("pytest -q"), out(NATIVE_FAIL + "1 failed"), call("pytest -q")]
        pairs, _ = _call_output_pairs(steps)
        self.assertEqual(sum("1 failed" in p[1] for p in pairs), 1)


class MissingCaptureTests(unittest.TestCase):
    """R2: a missing capture abstains for the claim it would decide."""

    def test_missing_piped_dbt_result_abstains(self):
        self.assertEqual(claims.uncovered_claims("dbt build passed.", [call("dbt build | tail -5", "d")]), [])

    def test_missing_piped_compile_result_abstains(self):
        self.assertEqual(claims.uncovered_claims("Compiled clean.", [call("dbt compile | tail -5", "d")]), [])

    def test_missing_deploy_observation_abstains(self):
        self.assertEqual(claims.uncovered_claims("Deployed example.py.", [call("stat /tmp/example.py", "s")]), [])
        self.assertEqual(claims.uncovered_claims(
            "Deployed to https://example.com.", [call("curl https://example.com", "c")]), [])

    def test_captured_unrelated_result_still_blocks(self):
        self.assertIn("pipeline/data claim",
                      claims.uncovered_claims("dbt build passed.", pair("true", NATIVE_OK, "t"))[0])
        self.assertTrue(claims.uncovered_claims(
            "Deployed example.py.", pair("true", NATIVE_OK, "t")))

    def test_inspection_pipe_does_not_count_as_attempt(self):
        steps = [call("rg 'dbt build' README.md | head -5", "r")]
        self.assertIn("pipeline/data claim", claims.uncovered_claims("dbt build passed.", steps)[0])


class PerAssertionUncertaintyTests(unittest.TestCase):
    """R3: uncertainty for one assertion never erases another's failure."""

    def test_uncertain_status_read_keeps_known_dbt_failure(self):
        steps = pair("dbt build", NATIVE_FAIL + "Done. PASS=0 ERROR=1", "d") + [
            call("cloud job status target-run", "p")]
        gaps = claims.uncovered_claims("dbt build passed. Pipeline target-run completed.", steps)
        self.assertIn("pipeline/data claim", gaps[0])

    def test_uncertain_status_read_alone_abstains(self):
        steps = pair("dbt build", NATIVE_OK + "Done. PASS=1 ERROR=0", "d") + [
            call("cloud job status target-run", "p")]
        self.assertEqual(claims.uncovered_claims(
            "dbt build passed. Pipeline target-run completed.", steps), [])


class OverlapSettlesTests(unittest.TestCase):
    """R4: binding resumes once an overlapping window settles."""

    def test_serial_failure_after_settled_overlap_blocks(self):
        steps = [call("true"), call("true"), out(NATIVE_OK), out(NATIVE_OK)] + pair(
            "dbt build", NATIVE_FAIL + "Done. PASS=0 ERROR=1")
        self.assertIn("pipeline/data claim", claims.uncovered_claims("dbt build passed.", steps)[0])
        pairs, _ = _call_output_pairs(steps)
        self.assertTrue(pairs[0][2].get("_capture_ambiguous"))
        self.assertIn("ERROR=1", pairs[2][1])

    def test_serial_success_after_settled_overlap_passes(self):
        steps = [call("true"), call("true"), out(NATIVE_OK), out(NATIVE_OK)] + pair(
            "dbt build", NATIVE_OK + "Done. PASS=1 ERROR=0")
        self.assertEqual(claims.uncovered_claims("dbt build passed.", steps), [])

    def test_call_started_during_overlap_joins_it(self):
        steps = [call("true"), call("true"), out(NATIVE_OK), call("dbt build"),
                 out(NATIVE_OK), out(NATIVE_FAIL + "Done. PASS=0 ERROR=1")]
        self.assertEqual(claims.uncovered_claims("dbt build passed.", steps), [])


class StatusRecordTests(unittest.TestCase):
    """R5: one record supplies both run identity and status."""

    def test_target_failed_record_blocks_despite_other_completed_record(self):
        body = ('{"id":"target-run"}\nINFO status response\n'
                '{"id":"old-run","status":"Completed"}\n{"id":"target-run","status":"Failed"}')
        steps = pair("cloud job status target-run", NATIVE_OK + body, "s")
        self.assertIn("pipeline/data claim", claims.uncovered_claims("Pipeline target-run completed.", steps)[0])

    def test_borrowed_completion_never_covers_target(self):
        for body in ('{"id":"target-run"}\nINFO status response\n{"id":"old-run","status":"Completed"}',
                     "{'id':'target-run'}\n{'id':'old-run','status':'Completed'}",
                     '[{"id":"target-run"},{"id":"old-run","status":"Completed"}]',
                     '{"id":"target-run"}\n{"id":"old-run","status":"Completed"}'):
            with self.subTest(body=body):
                from sage.pipeline_receipts import run_observation
                self.assertEqual(run_observation(NATIVE_OK + body, "target-run"), "unknown")
                steps = pair("cloud job status target-run", NATIVE_OK + body, "s")
                # Target status is not observed: abstain, neither pass nor block.
                self.assertEqual(claims.uncovered_claims("Pipeline target-run completed.", steps), [])

    def test_same_record_completion_passes_and_wrong_id_blocks(self):
        ok = pair("cloud job status target-run", NATIVE_OK + '{"id":"target-run","status":"Completed"}', "s")
        self.assertEqual(claims.uncovered_claims("Pipeline target-run completed.", ok), [])
        wrong = pair("cloud job status old-run", NATIVE_OK + '{"id":"old-run","status":"Completed"}', "s")
        self.assertIn("pipeline/data claim", claims.uncovered_claims("Pipeline target-run completed.", wrong)[0])

    def test_dict_and_array_records_select_claimed_run(self):
        from sage.pipeline_receipts import run_observation
        self.assertEqual(run_observation(
            "{'id':'target-run','status':'Failed'}\n{'id':'old-run','status':'Completed'}", "target-run"), "failure")
        self.assertEqual(run_observation(
            '[{"id":"old-run","status":"Failed"},{"id":"target-run","status":"Completed"}]', "target-run"), "success")


class CountRecordTests(unittest.TestCase):
    """R6: count records keep their boundaries."""

    COMMAND = 'bq query "select count(*) as source_count, count(*) as target_count"'

    def test_conflicting_records_are_not_merged(self):
        steps = pair(self.COMMAND, NATIVE_OK + '[{"source_count":120,"target_count":119},{"target_count":120}]', "q")
        self.assertIn("pipeline/data claim", claims.uncovered_claims("Row counts match.", steps)[0])

    def test_incomplete_records_abstain(self):
        steps = pair(self.COMMAND, NATIVE_OK + '[{"source_count":120},{"target_count":120}]', "q")
        self.assertEqual(claims.uncovered_claims("Row counts match.", steps), [])

    def test_single_records_still_decide(self):
        equal = pair(self.COMMAND, NATIVE_OK + '[{"source_count":120,"target_count":120}]', "q")
        unequal = pair(self.COMMAND, NATIVE_OK + '[{"source_count":120,"target_count":119}]', "q")
        self.assertEqual(claims.uncovered_claims("Row counts match.", equal), [])
        self.assertIn("pipeline/data claim", claims.uncovered_claims("Row counts match.", unequal)[0])

    def test_text_values_are_not_overwritten(self):
        steps = pair("sqlcmd -Q \"select count(*)\"",
                     NATIVE_OK + "source_count=120\ntarget_count=119\ntarget_count=120", "q")
        self.assertEqual(claims.uncovered_claims("Row counts match.", steps), [])


class RepairedFailureTests(unittest.TestCase):
    """R7: one success interpreter decides initial and superseding receipts."""

    def test_same_command_success_supersedes_dbt_failure(self):
        steps = pair("dbt build", NATIVE_FAIL + "Done. PASS=0 ERROR=1", "f") + pair(
            "dbt build", NATIVE_OK + "Done. PASS=76 WARN=9 ERROR=0", "s")
        self.assertIsNone(fail_support(steps, "dbt build passed."))

    def test_launcher_variant_supersedes_dbt_failure(self):
        for summary in ("Done. PASS=76 ERROR=0", "Done. PASS=76 WARN=9\nERROR=0"):
            with self.subTest(summary=summary):
                steps = pair("dbt build", NATIVE_FAIL + "Done. PASS=0 ERROR=1", "f") + pair(
                    "uv run dbt build", NATIVE_OK + summary, "s")
                self.assertIsNone(fail_support(steps, "dbt build passed."))

    def test_different_dbt_action_or_target_does_not_supersede(self):
        for later in ("dbt run", "dbt build --select other_model"):
            with self.subTest(later=later):
                steps = pair("dbt build --select example_model", NATIVE_FAIL + "Done. PASS=0 ERROR=1", "f") + pair(
                    later, NATIVE_OK + "Done. PASS=76 ERROR=0", "s")
                self.assertIsNotNone(fail_support(steps, "dbt build passed."))

    def test_unrepaired_failure_still_supports(self):
        steps = pair("dbt build", NATIVE_FAIL + "Done. PASS=0 ERROR=1", "f")
        self.assertIsNotNone(fail_support(steps, "dbt build passed."))


class ArtifactScopeTests(unittest.TestCase):
    """R8: artifact text supports FAIL only for the claimed operation or target."""

    def write(self, target, content):
        return [call("", "w", "write_to_file", {"TargetFile": target, "Content": content}), out("exit=0", "w")]

    def test_unrelated_artifact_is_not_dbt_failure_evidence(self):
        steps = pair("dbt build", NATIVE_OK + "Done. PASS=76 ERROR=0", "d") + self.write(
            "/tmp/example.txt", "FAILED example status")
        self.assertIsNone(fail_support(steps, "dbt build passed."))

    def test_clean_artifact_control(self):
        steps = pair("dbt build", NATIVE_OK + "Done. PASS=76 ERROR=0", "d") + self.write(
            "/tmp/example.txt", "example status")
        self.assertIsNone(fail_support(steps, "dbt build passed."))

    def test_claimed_target_artifact_with_failure_supports(self):
        steps = self.write("/tmp/report.md", "FAILED example status")
        self.assertIsNotNone(fail_support(steps, "Updated report.md and verified it."))


if __name__ == "__main__":
    unittest.main()
