# Round 6 Implementation Report

Commit `7cb08ae` on `main` of agy-jev-hooks. Parent `d546647`. Local only, not pushed, as Kai asked. Implementer: Claude fork agent. The lead saved this report from the agent's final reply.

Lead checks (2026-10-07): `uv run pytest -q tests` 810 passed, 2 skipped; hook and the 5 changed modules match `~/.config/agy/`; default block tags `""`; `realturn.py` re-run: `aa24dd5f`:35, `cf60f761`:0, `b70b1a2d`:37, `86d0b61a`:0 pass, `a0ba03ec`:4 blocks; `sweep.py` re-run at `7cb08ae`: 1,051 transcripts, 1,791 turns, 35 flagged.

## Finding Status

| Finding | Test name | Fix commit | Status |
|---|---|---|---|
| 1 | `BackgroundTaskTests` (status DONE covers pytest and dbt claims, finished system message covers `npm test`, unfinished or RUNNING task abstains, failed background task still blocks) | `7cb08ae` | Fixed. A "running as a background task" result is read from that task's completion record: the `Task id ... finished with result` system message and/or a `manage_task` status read with `Status: DONE` for the same task ID. If neither exists, the check abstains. A DONE record with no exit code and no explicit failure is uncertain: a pass summary can still cover the claim, but a missing summary is not a gap. |
| 2 | `UnknownRunnerTests` (tsx runner and unittest script, acceptance script, uncaptured script, `dbt compile` covers a parse claim, three controls that must still block); `NativeShapeTests` (quoted native `CommandLine`, uncaptured monitor script, `py_compile`) | `7cb08ae` | Fixed. The runner pattern knows `tsx --test`, `pnpm test`, `bun test`, `deno test`. Runner-agnostic pass summaries (`# pass N # fail 0`, `Ran N tests in Xs OK`, `N passed in Xs`, `Tests: N passed`) cover a test claim only when a shell call or `manage_task` read produced them. A script run (`bash x.sh`, `./x.sh`, `python3 x.py`, `node x.js`, `make`) is unknown when its result was not captured, or when it exited 0, mentions test/check/accept and shows no failure. The compile/parse action comes from the matched keyword; `dbt compile` covers a parse claim; `py_compile` and `compileall` are compile operations. |
| 3 | `RunIdRecordTests` (claim by run ID, by pipeline name, unnamed; another run still blocks); updated `test_pipeline_run_needs_status_readback_with_run_id` | `7cb08ae` | Fixed. ID keys match case- and `_`-insensitively: `runid`, `executionid`, `executionarn`, `id`. A claimed name also matches `pipelineName`, `jobName`, `workflowName`, `name`. A terminal status that names no run is unknown (abstain); the round-1 test changed to match review 5. |
| 4 | `NonTerminalStatusTests` (InProgress and Queued block a "completed" claim; a later Succeeded read covers) | `7cb08ae` | Fixed. `inprogress`, `in_progress`, `queued`, `running`, `notstarted`, `pending` contradict a completion claim. Missing or unknown status still abstains. |
| 5 | `NonClaimTextTests` (third-party deploy text, modal verb, "can review the published", reported Vietnamese screenshot, progressive screenshot check; controls: own deploy claim, "screenshot attached", "ảnh chụp đính kèm") | `7cb08ae` | Fixed. Not claims: text after `must`, `can`, `ought to`, `needs to`, `has to`, `have to`; "<ProperName> deployed" narrative; a screenshot mention whose only proof word was "chụp" inside "ảnh chụp". The agent's own deploy and screenshot claims still count. |
| 6 | `CountRetryAndRowTests.test_failed_query_then_successful_retry_covers`, `test_only_failed_queries_block` | `7cb08ae` | Fixed. A failed count query is skipped; a later successful retry decides. Blocks when every count query failed. |
| 7 | `CountRetryAndRowTests.test_second_table_row_mismatch_blocks` | `7cb08ae` | Fixed. Each text-table row is a record until a blank line or `rows affected` footer. All rows equal covers; any unequal row blocks. |

Real-turn shapes found by the sweep (`NativeShapeTests`): timers (`schedule`), file tools and `send_message` are not executions; a `manage_task` status read is not a pipeline readback; a test summary inside a viewed file does not cover a test claim.

Test-first evidence: the new test file run against an archive of `d546647` fails 19 of 28 checks, covering every finding; 9 pass as controls.

## Verification

```text
uv run pytest -q tests
810 passed, 2 skipped, 633 subtests passed in 19.63s

uv run ruff check sage/claim_receipts.py sage/claims.py sage/pipeline_claims.py sage/pipeline_receipts.py sage/claim_assertions.py tests/test_stop_audit_round6.py tests/test_claims.py
All checks passed!

git diff --check
exit 0
```

Module sizes: `pipeline_claims.py` 298, `claims.py` 265, `claim_receipts.py` 249, `pipeline_receipts.py` 250 lines.

## Live test

Harness: reviewer's, copied to `/tmp/round6/`. `sweep.py` runs the real CLAIM gate on every explicit user turn with an execution or edit across 1,051 transcripts (1,791 turns).

| Sweep | Total flags | Probe flags | Real flags | Added vs baseline | Removed vs baseline |
|---|---:|---:|---:|---:|---:|
| Baseline `d546647` | 78 | 9 | 69 | – | – |
| Final `7cb08ae` | 35 | 9 | 26 | 0 | 43 |

An intermediate sweep added 7 flags (`201152e9`:35/36/40, `558d34ee`:0, `6d991cb3`:0, `99a330ce`:5, `b3d4d233`:0) after timers stopped counting as attempts; fixed before the commit. The final sweep adds none.

`realturn.py` at the final HEAD (`AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM`):

```text
aa24dd5f 35 pass      b70b1a2d 58 pass      2dbae5ed 6 pass       c52ac8e0 7 pass
e28b2be8 0 pass       073c6f48 12 pass      b70b1a2d 37 pass      39a362e4 1 pass
3047017f 1 pass       f7ed2238 1 pass       f7ed2238 2 pass       cf60f761 0 pass
86d0b61a 0 pass       94f7dcef 0 pass       1dc74584 0 pass       5eb7e310 3 pass
2a2f1929 19 pass      7d323d9a 2 pass
a0ba03ec 4 BLOCKED (kept on purpose: no receipt shows the run succeeded)
```

Every false positive review 5 named by turn ID now passes. Each of the 43 removed turns was checked with `why.py`: a passing background run now read, a recognized runner summary, a script whose output shows the claimed result, or an uncaptured test/compile run (unknown). No removed turn has a receipt that contradicts the claim.

Probe regression: reviewer probes rounds 1-5 (`prior.py`, `edges.py`, `fail-and-regressions.py`): 88 labelled outcomes, 0 changed vs the reviewer's `d546647` outputs. Synthetic probes at final HEAD: A1-A3 pass; A5, A6 (InProgress) block; B1 passes; B3 blocks; D1, D1b, D1c, D3, E1, E2, G1-G4 pass; wrong-ID and failed-status controls block.

The 26 remaining real flags (not adjudicated, except where noted):
- Deploy claims with no target check (consistent with the contract): `03cd1966`:9, `03cd1966`:40, `0fe6788a`:2, `0fec3c1f`:27, `1b8ee3e1`:31, `1b8ee3e1`:44, `201152e9`:56, `26d9d153`:9, `6cd6eef0`:10, `aa24dd5f`:1, `c4f6f1a0`:3.
- Loose "published" wording: `50635adc`:9, likely false positive.
- Visual claims with screenshot checks: `03cd1966`:4, :43, :44.
- Carry-over test claims with no test run in that turn: `2dbae5ed`:3, `2dbae5ed`:4, `2677689e`:3, `d8d72af1`:5.
- Pipeline claim: `a0ba03ec`:4.
- Others: `49c31fb7`:15 (OKF validation phrased as a test), `75a02b9f`:14 (OfficeCLI layout check), `a7dffc0b`:0, `b0e9afd2`:1 (Synapse "test run Succeeded" routed to the test check), `c52ac8e0`:0 (its `npm test` exited 1 with `# fail 1`), `8a5e7558`:6 (narrates a video segment; likely false positive).

## Assumptions

- A background DONE record with no exit code and no explicit failure is uncertain; a pass summary in it still covers.
- An uncaptured script is unknown for test and pipeline claims.
- A successful test-shaped script is unknown only if its command or output mentions test, check, accept, verify, spec or smoke.
- Executions: `run_command`, `bash`, `exec`, `terminal`, `cmd`, `command`, `manage_task`, unnamed calls. Timers, file tools and messaging tools are not.
- Third-party deploy detection is a heuristic: a capitalized name directly before "deployed" is third-party, except `I`, `We`, `It`, `This`, `That`, `The`, `Code`, `App`, `Service`, `Build`. A misread abstains.
- `can`, `must` and similar before a claim keyword make it not a claim ("I can confirm tests pass" abstains).
- Several complete count rows are accepted only when every row is equal and the sentence states no expected pair.

## Open or Abstaining Paths

- A4 (Fabric `id`-only record, claim names a pipeline) still blocks; abstaining would also unblock the required wrong-ID control.
- A7 (`gh run view` text) and A8 (`-o table` status) still block; text-format status parser does not read these shapes.
- FAIL-only gaps unchanged: `cd proj && dbt build` and `-s` vs `--select` reruns do not supersede a failure. FAIL stays shadow.
- Per-gate fail-open unchanged: after a caught CLAIM exception, a FAIL steer can still fire if FAIL is set to block.
- Precision is measured only on this transcript snapshot; adjudication of the 26 remaining flags is partial.
