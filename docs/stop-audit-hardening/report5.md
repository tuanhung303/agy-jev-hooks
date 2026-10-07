# Round 5 Implementation Report

Commit `d546647` on `main` of agy-jev-hooks, local only (not pushed, as Kai asked). Parent `10860b2`. Default stays shadow: `AGY_STOP_AUDIT_BLOCK_TAGS` default is empty.

Implementer: Claude fork agent. The lead saved this report because the fork could not write report files.

## Finding Status

| Finding | Test name | Fix commit | Status |
|---|---|---|---|
| 1 | `ExplicitResultBindingTests` (R1 exact case, swapped case, ID-less dbt after explicit pytest, fully tagged control, result consumed once) | `d546647` | Fixed. Results that carry an explicit ID are never bound by position, and each result binds to one call only. An ID-less result that arrives while an explicit call is still open binds tentatively. The binding becomes firm only when that call's own ID result is present. |
| 2 | `MissingCaptureTests` (piped dbt, piped compile, missing `stat` and `curl` observations, captured unrelated control, inspection pipe control) | `d546647` | Fixed. Matching looks at the shell stage that actually runs: `dbt build \| tail -5` counts as a dbt attempt, and `rg 'dbt build' \| head` does not. A missing deploy check for the claimed target abstains. `host_of` now drops a trailing dot. |
| 3 | `PerAssertionUncertaintyTests` (R3 exact case, uncertain status read alone) | `d546647` | Fixed. Each pipeline claim is judged on its own receipts and its own uncertainty. |
| 4 | `OverlapSettlesTests` (R4 exact case, success after overlap, call joining an open overlap) | `d546647` | Fixed. Open ID-less calls that receive results form an overlap group. The group closes after one result per call, and serial binding resumes. A call that starts while a group is open joins that group. |
| 5 | `StatusRecordTests` (R5 exact case, four borrowing formats including exact E7, same-record and wrong-ID controls, dict and array record selection) | `d546647` | Fixed. `records()` keeps JSON lines, dict lines and arrays as whole records and skips log lines. `run_observation()` reads only the claimed run's records: failure-only blocks, success-only covers, anything else abstains. The round 4 test `test_status_id_are_never_borrowed_from_different_records` now expects abstention, as review 4 required. |
| 6 | `CountRecordTests` (R6 exact case, incomplete records abstain, single-record controls, repeated text label) | `d546647` | Fixed. `count_records()` returns one label set per record or table row. A complete unequal record blocks. One-sided records or a repeated text label with different values abstain. Records are never merged. |
| 7 | `RepairedFailureTests` (R7 exact same command, `uv run` variant with one-line and split summaries, different dbt action or selector, unrepaired control) | `d546647` | Fixed. One `_outcome()` interprets the first receipt and the superseding rerun. `WARN=9 ERROR=0` is not failure text. Commands are compared without `$ ` and launchers (`uv run`, `poetry run`, `python -m`). dbt commands match only on the same action and selector/target flags. |
| 8 | `ArtifactScopeTests` (R8 exact case, clean control, claimed-file artifact still supports) | `d546647` | Fixed. File content supports FAIL only when the claim names that file, or when the claim is a plain completion with no named operation and no file. |

## Verification

```text
uv run pytest -q tests
787 passed, 2 skipped, 627 subtests passed in 18.40s

uv run ruff check sage/claim_receipts.py sage/claims.py sage/pipeline_claims.py sage/pipeline_receipts.py sage/jev/verdict/support.py tests/test_stop_audit_round5.py tests/test_claims.py
All checks passed!

git diff --check
exit 0
```

- The post-commit hook synced the installed copies; all five changed modules under `~/.config/agy/sage/` match the repo.
- `hooks/agy-stop-audit.py` did not change and matches the installed copy.
- To stay under the 300-line module limit in `tests/test_static_analysis.py`, `has_execution_or_edit`, `MUTATING_TOOL_NAMES` and the shell-stage helpers moved from `pipeline_claims.py` to `claim_receipts.py`. `pipeline_claims.py` re-exports `has_execution_or_edit`. The duplicate `_call_text` was replaced by `claim_receipts._cmd_text`.

## Live test

Offline replays through the real `hooks/agy-stop-audit.py` `main()`, using a copy of the reviewer's harness from `/tmp/review60a3df8/`. `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM,FAIL` was set only inside the replay process. Compass was stubbed; FAIL rows inject the `claim_conflict` label. Evidence assembly and `label_support` ran for real. Logs and counters were off.

| Finding | Review reproduction | Result | Required | Control | Result | Required |
|---|---|---|---|---|---|---|
| 1 | Explicit failed `false` (id a) plus ID-less `pytest -q` success, `Tests pass.` | NOT BLOCKED | NOT BLOCKED | Fully ID-tagged | NOT BLOCKED | NOT BLOCKED |
| 2 | `dbt build \| tail -5` with no result, `dbt build passed.` | NOT BLOCKED | NOT BLOCKED | Plain `dbt build` with no result | NOT BLOCKED | NOT BLOCKED |
| 3 | Failed dbt plus result-less `cloud job status target-run`, two assertions | BLOCKED | BLOCKED | Failed dbt alone | BLOCKED | BLOCKED |
| 4 | Two overlapping `true` calls, then a serial failed dbt | BLOCKED | BLOCKED | Failed dbt without the earlier overlap | BLOCKED | BLOCKED |
| 5 | Target record without status, log line, old-run Completed, target Failed | BLOCKED | BLOCKED | Same-record target Completed | NOT BLOCKED | NOT BLOCKED |
| 6 | `[{"source_count":120,"target_count":119},{"target_count":120}]` | BLOCKED | BLOCKED | Single unequal record | BLOCKED | BLOCKED |
| 7 (FAIL) | Failed dbt, then same command with `Done. PASS=76 WARN=9 ERROR=0` | NOT BLOCKED | NOT BLOCKED | Split-line `ERROR=0` rerun | NOT BLOCKED | NOT BLOCKED |
| 8 (FAIL) | Successful dbt plus an unrelated `/tmp/example.txt` write containing `FAILED example status` | NOT BLOCKED | NOT BLOCKED | Clean artifact | NOT BLOCKED | NOT BLOCKED |

Regression sweep: the reviewer's 94 probes from rounds 1-4 (`prior.py`, `edges.py`, `fail-and-regressions.py`) were re-run against the `60a3df8` baseline. 14 probes changed outcome, each one a targeted defect (X1 x3, X2, X3 x3, X4, X6 conflicting records now block, exact E7 now abstains, F7 repaired variant and exact repair now pass, F8 unrelated artifact now passes). Controls kept their outcome: Q&A turns, fullyIdle=False, steer cap, E1 serial and mixed-ID failures, unrepaired dbt and pytest failures, "R2 current full write" FAIL control.

Natural replay (probe prompts filtered; none found):

| Cohort | Sessions | Call slots | Bound | Uncertain | Empty unmarked | Blocked |
|---|---:|---:|---:|---:|---:|---:|
| Review 4 sample | 15 | 718 | 712 | 6 | 0 | 0 |
| Newer sessions | 13 | 1,244 | 1,238 | 6 | 0 | 0 |

- `f5edeb43` went from 3 bound / 36 ambiguous to 33 / 6.
- `33d1187a` changed after review 4 (final turn now has 3 calls instead of 23), so the cohort has 718 slots, not 738.
- None of the 28 sessions blocks, makes a pipeline or count claim, or has FAIL support. Pipeline CLAIM precision and FAIL precision remain unmeasured on real traffic.

## Assumptions

- A status read with a record for the claimed run but no terminal status abstains.
- A status with no run ID at all still counts as "no readback" and blocks (keeps `test_pipeline_run_needs_status_readback_with_run_id`).
- If the claimed run has both success and failure records, the check abstains.
- If the claim names no run ID and the output has several ID records, the claim is covered only when all succeeded; otherwise abstain.
- A plain completion claim with no named operation and no file (for example `Done.`) still accepts failure text in files written this turn.
- Claims about a named operation count file content only when the claim names that file.
- dbt commands are the same operation when they share the action and `-s/--select`, `-m/--models`, `--exclude`, `--selector`, `-t/--target`. Other flags do not matter.
- An ID-less result that arrives while an explicit call is open binds tentatively; it becomes firm only if every explicit call open at that moment gets its own ID result in the turn.

## Open or Abstaining Paths

- No finding is left open.
- By design these now abstain: a result that cannot be tied to the claimed run, one-sided count records, conflicting text counts.
- Out of scope and unchanged: after a caught CLAIM exception, a configured FAIL steer can still fire when CLAIM,FAIL blocking is set explicitly. The default is shadow.
