# Round 4 implementation report

## Finding status

| Finding | Test name | Fix commit | Status |
|---|---|---|---|
| 1 | `test_serial_native_idless_windows_bind_each_result`; `test_explicit_failed_operation_survives_unrelated_idless_ambiguity`; `test_natural_multicall_turn_binds_serial_mixed_receipts` | `60a3df8` | Fixed. Serial native windows bind per call; overlapping calls stay uncertain locally. Explicit ID-bound failure remains actionable. |
| 2 | `test_native_count_table_maps_values_by_header_column`; `test_structured_and_difference_count_receipts_are_supported`; `test_unrecognized_count_schema_abstains` | `60a3df8` | Fixed. Header positions are honored, structured and difference receipts are parsed, unknown schemas abstain. |
| 3 | `test_inspection_text_does_not_cancel_opaque_wrapper_uncertainty` | `60a3df8` | Fixed. Inspection text cannot establish execution identity or cancel wrapper uncertainty. |
| 4 | `test_successful_dbt_summary_is_not_failure_support`; `test_multiline_command_comment_is_not_failure_output` | `60a3df8` | Fixed. Failure support reads output separately from command text and parses dbt error counts. |
| 5 | `test_repaired_failure_under_equivalent_runner_command_is_superseded`; `test_unrelated_failure_does_not_support_successful_dbt_claim`; `test_failure_for_different_claimed_path_is_not_support` | `60a3df8` | Fixed. Support must match the claimed operation and target; equivalent successful reruns supersede older failures. |
| 6 | `test_compile_error_text_overrides_zero_exit_pipe` | `60a3df8` | Fixed. Explicit compiler diagnostics override a successful pipe status; zero-error summaries remain valid. |
| 7 | `test_status_id_are_never_borrowed_from_different_records` | `60a3df8` | Fixed. ID and status must be established by the same record. |
| 8 | `test_missing_result_is_unauditable_for_attempted_operation` | `60a3df8` | Fixed. Missing capture abstains for that operation; captured evidence for a different command can still establish a gap. |

## Verification

```text
uv run pytest -q tests
757 passed, 2 skipped, 618 subtests passed in 8.45s

uv run ruff check sage/claim_receipts.py sage/claims.py sage/pipeline_receipts.py sage/pipeline_claims.py sage/jev/verdict/support.py tests/test_claims.py tests/test_jev_compass.py
All checks passed!

git diff --check
exit 0
```

The tests include the eight review reproductions and opposite controls. The full suite retained the prior controls. The working tree has only the pre-existing untracked `demo-video/.env`.

## Live test

Offline replays invoked the real `hooks/agy-stop-audit.py` `main()` with `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM,FAIL` set only in the replay process. CLAIM used the real deterministic claim gate. For FAIL rows, the Jev label choice was stubbed to `claim_conflict`; evidence assembly and `label_support` were real. Transcript input used in-memory steps. Stop counters and logs were disabled. `BLOCKED` means the hook returned `decision: continue`; `NOT BLOCKED` means it returned `{}`.

| Finding | Review reproduction | Result | Control | Result |
|---|---|---|---|---|
| 1 | Two serial ID-less `true` calls, then `dbt build passed.` | BLOCKED | Captured successful `dbt build` summary | NOT BLOCKED |
| 2 | Equal three-column count table with leading `batch_id` | NOT BLOCKED | Unequal source and target counts | BLOCKED |
| 3 | Opaque successful dbt wrapper plus `rg` inspection mentioning `dbt build` | NOT BLOCKED | Opaque wrapper without inspection | NOT BLOCKED |
| 4 | Successful `PASS=76 WARN=9 ERROR=0` summary offered as FAIL support | NOT BLOCKED | Captured dbt failure | BLOCKED |
| 5 | Unrelated Python import failure alongside successful dbt claim | NOT BLOCKED | Failure from the claimed dbt operation | BLOCKED |
| 6 | Exit 0 with `Compilation Error` and missing ref | BLOCKED | `Found 0 errors.` with exit 0 | NOT BLOCKED |
| 7 | Target ID and `Completed` status split across JSON records | BLOCKED | Target ID and `Completed` status in one JSON record | NOT BLOCKED |
| 8 | Attempted dbt call with no captured result | NOT BLOCKED | Captured `true` result with dbt success claim | BLOCKED |

Additional command-text replay: `# 1 failed reproduction` inside a multiline Python command with exit 0 and output `ok` was NOT BLOCKED by the FAIL gate.

## Publish and installed copy

Commit `60a3df80ed038f399bc68ff9b2f614ebae0ec583` was pushed to `origin/main`. After push, local `HEAD` and `origin/main` both resolved to that SHA. The post-commit installer copied the changed modules. Byte comparisons passed for the stop hook and all five changed shared source modules under `~/.config/agy`.

The post-commit installer also ran its `hooks.json` verification/write step. The dispatch boundary prohibited manual edits to `hooks.json`; none were made manually. The file was not byte-snapshotted before commit, so this run cannot establish whether its bytes changed.

Default behavior remains shadow. No hook mode, steer cap, Compass budget, other repository source, or live agy session was changed.
