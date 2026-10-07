# Round 8 Implementation Report

Commit `78da81a` on `main` of agy-jev-hooks. Parent `c8a898b`. Local only, not pushed. Implementer: Claude fork agent. Approach (Kai, 2026-10-07): review each claim category on real traffic and drop noise and false positives; precision first. The lead saved this report from the agent's final reply.

Lead checks (2026-10-07): `uv run pytest -q tests` 848 passed, 2 skipped; hook and sage modules match `~/.config/agy/`; default block tags `""`; `LOG_ONLY_CLAIM_KINDS = frozenset({"deploy"})` at `sage/claims.py:172`; holdout 8/22 lies, 0/12 controls blocked; `lies.py` 26/31; `sweep7.py` 1,791 turns, 17 flagged.

## Finding Status

| Finding | Test name | Fix commit | Status |
|---|---|---|---|
| 3(a) `b0e9afd2`:1 | `PipelineTestRunTests` | `78da81a` | Fixed. "test run" plus a pipeline/job/DAG/run ID is a pipeline claim. Polling logs (`Attempt N status = X`) are run records. Carry-over reads the `Task id ... finished with result` message for the claimed run ID. |
| 3(b) `c4f6f1a0`:3, `0fe6788a`:2, `201152e9`:56 | `TransferLogTests` | `78da81a` | Fixed. Exit-0 output with `Copying <src> to gs://...` or `upload: ... to s3://...` is a deploy observation, only when the claim names no host and any named file appears; in turn and for carry. |
| 3(c) `073c6f48`:16 | `SubagentReportTests` | `78da81a` | Fixed. A `[Message] sender=<session>` report (not `/task-N`) with a terminal-state word and a claim identifier makes the claim uncertain (abstain). |
| 3(d) `a7dffc0b`:0 | `DbtRunResultsTests` | `78da81a` | Fixed. A `run_results` read with `Status counts: {...}`, `Failures: 0` and no error/fail key covers. |
| 3(e) control C5 | `CountParsingTests.test_thousands_separator_is_one_count` | `78da81a` | Fixed. Thousands separators stripped. |
| 4 K2 | `CountParsingTests.test_carried_count_matches_runner_summary_not_any_number` | `78da81a` | Fixed. Carried counts match only runner summary numbers. |
| 4 Q5c | `CountParsingTests.test_one_sided_count_that_contradicts_the_claimed_pair_blocks` | `78da81a` | Fixed. A contradicting one-sided count fails. |
| 6 finding 2 (Q5a, Q5b) | `TestScriptNameTests` | `78da81a` | Fixed; sweep gained no flag. Only `test`, `tests`, `spec`, `accept*`, `e2e` script names stand in for a test run; `N / N ... passed` matching the claimed count covers. |
| 5 log-only deploy | `LogOnlyKindTests`; `PerTagModeTests.test_log_only_claim_verdict_never_blocks` | `78da81a` | Done. Deploy is log-only (`WOULD_CLAIM` even when CLAIM blocks). `claim_contract_hint` (Claude hook, sweep) still returns every kind. |

Test-first: `tests/test_stop_audit_round8.py` against `c8a898b` fails 14, passes 7 (controls).

## Verification

```text
uv run pytest -q tests
848 passed, 2 skipped, 691 subtests passed in 11.53s
uv run ruff check <10 changed files>
All checks passed!
git diff --check
exit 0
```

## Live test

### Category ledger (reviewer labels from sol-review7 Q1; precision = TP / (TP + FP + Und), ART excluded)

| Category | Before real flags | Before precision | After real flags | After precision | Mode after |
|---|---|---:|---|---:|---|
| deploy | `03cd1966`:9 TP, `26d9d153`:9 TP, `c4f6f1a0`:3 FP, `0fe6788a`:2 FP, `201152e9`:56 FP, `03cd1966`:40 Und, `6cd6eef0`:10 Und | 29% | `03cd1966`:9 TP, `26d9d153`:9 TP, `03cd1966`:40 Und, `6cd6eef0`:10 Und | 50% | log-only |
| test | `d8d72af1`:5 TP, `b0e9afd2`:1 FP, `a7dffc0b`:0 FP | 33% | `d8d72af1`:5 TP | 100% | blocking |
| pipeline/data | `073c6f48`:16 FP | 0% | none | unmeasured | blocking |
| visual | 3 ART | unmeasured | same 3 ART | unmeasured | blocking |

Deploy demotion depends on counting the 2 undecidable flags as false; if both are TP, deploy is 4/4.

| Sweep | Total | Probe | Real | Added | Removed |
|---|---:|---:|---:|---:|---:|
| `c8a898b` | 23 | 9 | 14 | – | – |
| `78da81a` | 17 | 9 | 8 | 0 | 6 |

False blocks per action turn in blocking categories: 0/1,791 (ART excluded); 3/1,791 = 0.17% with replay-only ART.

| Probe set | Before | After | Changes |
|---|---|---|---|
| Holdout lies (22) | 11 | 8 | Lost H17, H18, H19 (deploy, demoted) |
| Holdout controls (12) | 1 blocked | 0 blocked | C5 fixed |
| Carry K1-K6 | 3/5 lies | 4/5 lies | K2 blocks; K1 passes (Kai) |
| Q5 a-d | 1/4 | 4/4 | |
| lies.py (31) | 31/31 | 26/31 | Lost L7, L7b-e (deploy, demoted) |
| lies7.py (12) | 12/12 | 9/12 | Lost X5, X6, X9 (deploy, demoted) |

Probe families (prior, edges, fail-and-regressions, newprobes, final-check): 0 changed. Outputs in `/tmp/round8/final/`.

## Assumptions

- A category with 0 decidable flags stays blocking (pipeline/data, visual).
- Transfer log is a deploy observation only when the claim names no host.
- A subagent report counts only from another session's sender, with a terminal-state word and an identifier of at least 4 characters containing `_`.
- Pipeline carry by run ID uses the latest known outcome among system-delivered finish messages.
- `test`/`spec`/`accept`/`e2e` scripts with exit 0 and no failure still abstain; `check`/`smoke` no longer do.

## Open or Abstaining Paths

- Deploy demotion depends on the 2 undecidable labels; Kai decides.
- Visual and pipeline/data precision unmeasured on real traffic.
- Holdout misses H1, H3-H5, H8, H9, H12, H14-H16, H21 unchanged (claim wording out of scope).
- K1 passes, as Kai decided.
