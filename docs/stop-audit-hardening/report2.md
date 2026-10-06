# agy Stop audit repair report

## Result

All eight reproduced defects were fixed in `bcb550381c0d1befee322359758c81f98589fb15` (`fix(agy): harden claim receipt verification`). The commit is on `main` and pushed to `origin/main`. The default block-tag list remains empty, so CLAIM and FAIL stay in shadow mode.

The post-commit hook ran `scripts/sync.sh`. The installed agy Stop hook and all changed claim modules under `~/.config/agy/sage` match the repository files byte-for-byte. The Gemini hook copy also matches `hooks/agy-stop-audit.py`.

The sync invokes `scripts/install.sh`, which serializes `~/.gemini/config/hooks.json`. It refreshed that file's timestamp. I did not edit the file directly. The agy-stop-audit, command-timer, and skill-recommender entries match the available October 5 backup. No registration or hook configuration change was part of this commit.

## Findings

| Finding | Test name | Fix commit | Status |
|---|---|---|---|
| 1 | `test_fail_gate_requires_current_turn_execution_or_edit`; `test_earlier_turn_artifact_failure_is_not_current_failure_support` | `bcb5503` | Fixed. CLAIM and FAIL share a current-turn action guard. Earlier-turn artifact failures cannot support FAIL. |
| 2 | `test_dbt_assertion_ignores_fenced_examples_escaped_quotes_and_keeps_inline_command` | `bcb5503` | Fixed. Fenced and escaped examples are ignored; inline command assertions remain detectable. |
| 3 | `test_native_agy_exit_status_covers_compile_and_build`; `test_native_nonzero_status_overrides_passing_test_summary` | `bcb5503` | Fixed. Shared status parsing accepts native agy headers and gives explicit nonzero status precedence. |
| 4 | `test_delayed_native_results_pair_across_planner_records_and_ambiguous_capture_abstains` | `bcb5503` | Fixed. Delayed results pair across planner records; incomplete or duplicate attribution abstains. No captured result remains unsupported. |
| 5 | `test_pipeline_readback_must_match_run_and_terminal_status` | `bcb5503` | Fixed. Named runs require the same readback ID and a terminal success status. Vietnamese completion claims require a matching dbt operation. |
| 6 | `test_count_reconciliation_requires_equal_labels_or_zero_difference` | `bcb5503` | Fixed. Counts require equal labelled source and target values or a zero-difference result. Contradictory stated counts fail. |
| 7 | `test_dbt_summary_normalizes_ansi_and_newlines_and_routes_dbt_test` | `bcb5503` | Fixed. ANSI and multiline dbt summaries are parsed; dbt test claims use dbt receipts. |
| 8 | `test_auditable_wrapped_api_and_sql_file_receipts_are_supported`; `test_unrecognized_execution_wrappers_abstain_from_missing_receipt_blocks` | `bcb5503` | Fixed. Auditable API and SQL-file executions can support claims. Literal output is excluded; unauditable wrappers abstain. |

## Verification

Command: `uv run pytest -q tests`

```text
729 passed, 2 skipped, 609 subtests passed in 8.33s
```

Command: `uv run ruff check hooks/agy-stop-audit.py sage/claims.py sage/claim_assertions.py sage/claim_receipts.py sage/pipeline_claims.py sage/jev/evidence/attribution.py sage/jev/verdict/support.py tests/test_agy_stop_audit_contract.py tests/test_claims.py tests/test_jev_compass.py`

```text
All checks passed!
```

`git diff --check` passed before commit. `git ls-remote origin refs/heads/main` returned `bcb550381c0d1befee322359758c81f98589fb15`. The working tree is clean except the pre-existing untracked `demo-video/.env`, which was not touched or committed.

## Offline live-hook replay

I ran the actual `hooks/agy-stop-audit.py` against temporary transcript files, with Compass stubbed and hook logs and counters redirected to a temporary directory. No agy session was started.

```text
F1 no current action: exit=0, blocked=False, output={}
F2 fenced reproduction: exit=0, blocked=False, output={}
F3 native compile success: exit=0, blocked=False, output={}
F5 wrong run readback: exit=0, blocked=True, output={"decision": "continue", "reason": "[agy-stop-audit] jev_compass not_verified: A material outcome lacks evidence matching its current target and version, without an established contradiction. Evidence: pipeline/data claim: no matching run, compile, or count-query receipt"}
F5 matching run readback: exit=0, blocked=False, output={}
F6 mismatched counts: exit=0, blocked=True, output={"decision": "continue", "reason": "[agy-stop-audit] jev_compass not_verified: A material outcome lacks evidence matching its current target and version, without an established contradiction. Evidence: pipeline/data claim: no matching run, compile, or count-query receipt"}
F6 equal counts: exit=0, blocked=False, output={}
F7 ANSI dbt test success: exit=0, blocked=False, output={}
F7 wrong runner: exit=0, blocked=True, output={"decision": "continue", "reason": "[agy-stop-audit] jev_compass not_verified: A material outcome lacks evidence matching its current target and version, without an established contradiction. Evidence: pipeline/data claim: no matching run, compile, or count-query receipt"}
```

## Precision and remaining limit

The prior review's natural FAIL precision remains unmeasured because its available log snapshot contained no FAIL events. The default remains shadow. This work does not re-enable blocking.
