# agy Stop audit repair report, round 3

## Result

All eight findings from `sol-review2.md` are fixed. Five more receipt edge cases found during independent review were also corrected, along with ambiguous pairing for multiple ID-less calls in one planner response. The default block-tag list remains empty, so CLAIM and FAIL stay in shadow mode. No manual hook configuration or installed-copy edits were made. The configured post-commit sync ran, and no live agy session was started.

Three read-only review passes found no remaining actionable defects. Committed on `main` as `733abe7313a1dfcd6116cfd4cd3b1652c9c1fb51` and pushed to `origin/main`. The remote branch resolves to the same SHA.

## Findings

| Finding | Test name | Fix commit | Status |
|---|---|---|---|
| 1. SQL table and footer counts | `test_sql_count_tables_skip_separators_and_never_count_footers_as_values` | `733abe7` | Fixed. Table parsing skips optional separator rows and footers. Only labeled comparisons or standalone scalar result rows can establish equality. |
| 2. Native-wrapped status JSON | `test_native_and_python_dict_status_readbacks_parse_id_and_status_together`; `test_native_output_header_does_not_hide_status_json` | `733abe7` | Fixed. Known native headers are removed before parsing JSON or safe Python dict output. ID and status are read from the same record. |
| 3. Vietnamese completion routing | `test_vietnamese_pytest_completion_uses_test_receipt`; `test_vietnamese_dbt_completion_after_action_still_requires_dbt_receipt` | `733abe7` | Fixed. Vietnamese pytest completion uses the test receipt. Both Vietnamese dbt word orders require a matching dbt receipt. |
| 4. Repaired and inherited FAIL evidence | `test_fail_support_discards_superseded_failure_for_same_command`; `test_fail_support_ignores_inherited_content_after_uncaptured_patch` | `733abe7` | Fixed. A later successful same-command rerun supersedes its failure. An uncaptured patch clears inherited file content from evidence. |
| 5. Cross-planner and same-batch ID-less binding | `test_idless_results_from_overlapping_planners_are_ambiguous`; `test_idless_calls_in_one_parallel_planner_batch_are_ambiguous` | `733abe7` | Fixed. Multiple ID-less calls are ambiguous whether they appear in one planner response or several. No positional success receipt is assigned. Single ID-less calls and explicit IDs remain supported. |
| 6. Unknown wrapper suppresses explicit failure | `test_explicit_dbt_failure_is_not_hidden_by_unrelated_unknown_wrapper`; `test_unrecognized_execution_wrappers_abstain_from_missing_receipt_blocks` | `733abe7` | Fixed. An unrelated unknown wrapper cannot erase a captured failure for the claimed operation. Opaque wrappers keep missing evidence inconclusive. |
| 7. Structured result status discarded | `test_structured_failure_status_is_preserved_with_receipt`; `test_structured_failure_status_invalidates_pipeline_readback` | `733abe7` | Fixed. Paired receipts retain the result step. Structured `exit_code` and `isError` status takes precedence over passing output. |
| 8. Zero-error compiler output | `test_zero_error_compiler_summary_is_success_but_positive_errors_fail` | `733abe7` | Fixed. `Found 0 errors.` can support success only when no known nonzero status contradicts it. Positive errors fail. |

## Verification

`uv run pytest -q tests`:

```text
742 passed, 2 skipped, 618 subtests passed in 8.32s
```

`uv run ruff check sage/claim_receipts.py sage/claims.py sage/jev/evidence/assemble.py sage/jev/verdict/support.py sage/pipeline_claims.py sage/pipeline_receipts.py tests/test_claims.py tests/test_jev_compass.py`:

```text
All checks passed!
```

`git diff --check` passed. The repository's required staged-diff scan found only the identifier `_RUN_FAILED_TOKEN_RE`, no secret value or sensitive project data.

## Live test

These are offline replays through the real `hooks/agy-stop-audit.py`, using temporary transcripts and state directories. `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM` was set only in the replay process. Jev classification was stubbed; for finding 4 the stub exercised the actual evidence assembler and local support function. No network classifier or persistent hook state was used.

```text
F1 footer mismatch: BLOCKED (exit=0)
F1 SQL equal table: not blocked (exit=0)
F2 native Output JSON: not blocked (exit=0)
F2 wrong run control: BLOCKED (exit=0)
F2 Python dict output: not blocked (exit=0)
F3 Vietnamese pytest: not blocked (exit=0)
F3 dbt after true, Vietnamese order A: BLOCKED (exit=0)
F3 dbt after true, Vietnamese order B: BLOCKED (exit=0)
F4 repaired failure: not blocked (exit=0)
F4 current failure control: not blocked (exit=0)
F5 overlapping ID-less: not blocked (exit=0)
F5 ID-bound failed control: BLOCKED (exit=0)
F5 same-batch reversed ID-less results: not blocked (exit=0)
F6 explicit dbt fail with unrelated wrapper: BLOCKED (exit=0)
F6 opaque wrapper: not blocked (exit=0)
F7 structured pipeline exit 1: BLOCKED (exit=0)
F7 structured pipeline exit 0: not blocked (exit=0)
F8 zero errors, exit 1: BLOCKED (exit=0)
F8 zero errors, exit 0: not blocked (exit=0)
```

FAIL remains shadowed. The current-failure control produced local support in the Compass stub but did not steer under the configured CLAIM-only block policy. Repaired failures produced no local FAIL support.

## Independent review

Three read-only review passes covered the eight requested findings, five follow-up reproductions, and the single-batch ID-less ordering edge. The reviewers reran the full suite and reported no actionable defects remaining. The final review also confirmed that single ID-less calls and explicit ID-bound calls still pair correctly.


## Shipping and workspace

The post-commit sync completed. The installed agy hook and changed claim modules under `~/.config/agy/sage` match the repository byte for byte. Hook registrations in `~/.gemini/config/hooks.json` match both available backups; the post-commit installer refreshed the file. No manual config or installed-copy edit was made.

`gh run list --branch main --limit 10 --json headSha,status,conclusion,name,event` returned `[]`, so no GitHub workflow run was available to inspect. The repository is on `main` at `733abe7313a1dfcd6116cfd4cd3b1652c9c1fb51`, tracking the same origin tip. The only untracked path is the pre-existing `demo-video/.env`; it was not opened, staged, or changed.
