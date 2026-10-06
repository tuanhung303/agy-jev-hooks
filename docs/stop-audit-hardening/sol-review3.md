keep shadow with fixes

| Finding in sol-review2.md | Status against 733abe7 | Own reproduction and remaining limit |
|---|---|---|
| 1. SQL table parsing and footer equality | Partly closed | The exact equal two-column sqlcmd table now passes; separate 120/119 scalar outputs with `(1 rows affected)` now block. Labelled 120/120 and `difference=0` controls pass. A leading SQL metadata column still shifts count attribution, and JSON/difference-table receipts still false-block. See finding 2 below. |
| 2. Native-wrapped Fabric JSON/dict | Partly closed | Native Created/Completed/Output headers plus a single Completed JSON record or Python dict now pass. The actual requests command also passes; wrong ID and structured failure controls block. Separate fallback regexes can still combine an ID and status from different records. See finding 7. |
| 3. Ordinary Vietnamese completion routed to dbt | Closed for the reviewed cases | `Em đã chạy xong pytest, tests pass.` with a passing pytest receipt passes. Both `Em đã chạy xong dbt build.` and `dbt build đã chạy xong.` after only `true` block. Generic unidentified completion now abstains. |
| 4. FAIL support from repaired/stale evidence | Partly closed | Exact same-command pytest failure followed by success now has no local FAIL support. An earlier full write containing `FAILED example status`, then a current uncaptured patch, also has no support; the current full-write control still has support. A successful rerun under another command spelling does not supersede the failure, support is not claim-target matched, and successful dbt output itself can supply false failure support. See findings 4 and 5. |
| 5. Cross-planner positional misbinding | Closed by abstention | Both reversed-completion reproductions now abstain, as do unequal result counts, duplicate IDs and multiple ID-less calls in one planner batch. ID-bound delayed results still pass. However, every multi-call native turn is treated as ambiguous, even sequential ones; this suppresses test/pipeline gap findings across the natural sample. See finding 1. |
| 6. Blanket wrapper abstention erases explicit failure | Partly closed | The exact failed dbt build plus unrelated Python cwd wrapper now blocks; an opaque custom-client wrapper still abstains. A harmless inspection containing the asserted command text can now cancel uncertainty and cause a false block on a successful wrapped run. See finding 3. |
| 7. Structured status discarded | Closed for the reviewed cases | Pytest `12 passed` with `exit_code=1` or `isError=True` blocks; exit 0 passes. Completed Fabric JSON with either structured failure flag blocks; exit 0 passes. Status is retained in the paired result. |
| 8. Zero-error compiler summary rejected | Partly closed | Native exit 0 plus `Found 0 errors.` passes; positive errors and nonzero status still block. The fix removed the broader failure-keyword check, so `Compilation Error` behind a zero-exit pipe now passes. See finding 6. |

## Precision

| Session | CLAIM verdict | One-line reason |
|---|---|---|
| `6ccbf8af` | No flag | Hook-review report; 12 retained call slots, all outputs suppressed by multi-call ID-less ambiguity. |
| `70bc69fa` | No flag | Hook audit; 42 retained call slots, all outputs suppressed by the same ambiguity rule. |
| `160cf9cc` | No flag | Independent audit; 59 retained call slots, all outputs suppressed. |
| `33d1187a` | No flag | Chart update; 23 retained call slots, all outputs suppressed; no recognized pipeline assertion. |
| `e1ac6811` | No flag | Hook review; 52 retained call slots, all outputs suppressed. |
| `7dd3726a` | No flag | Hook review with quoted reproductions; 50 retained call slots, all outputs suppressed. |
| `1f4add25` | No flag | Meeting brainstorming; five retained call slots, no recognized execution-success assertion. |
| `f48cc75e` | No flag | Former fenced-example false positive remains excluded; 36 retained call slots, all outputs suppressed. |
| `c5d13e63` | No flag | Forecast-method comparison; 12 retained call slots, no recognized execution-success assertion. |
| `ec0ee964` | No flag | Ingestion repair report; 91 retained call slots, all outputs suppressed. |
| `b0a9775a` | No flag | Cleanup report; 143 retained call slots, all outputs suppressed. |
| `eabee714` | No flag | Ingestion implementation report; 117 retained call slots, all outputs suppressed. |
| `24241126` | No flag | Document layout review; 19 retained call slots, all outputs suppressed. |
| `ed99c75d` | No flag | Document artifact review; 38 retained call slots, all outputs suppressed. |
| `f5edeb43` | No flag | Document-generator repair; 39 retained call slots, now zero paired outputs instead of the previous review's 39. |

**CLAIM replay: 0 true-positive flags, 0 false-positive flags, 15 unflagged final turns.** Prospective false blocks in this enumerated snapshot are **0/15 = 0%**. Precision among flags is **undefined, 0/0**, not 100%. This is not a recall estimate or a certification of unflagged reports.

Method: enumerated 1,051 transcripts under the two roots used by `resolve_transcript_path`, ordered by modification time, and selected the latest 15 completed final turns containing execution/edit calls. Replayed the complete captured final reply against `claim_contract_hint_for(..., require_action=True, steps=current_turn_steps(...))`, then the real hook with persistent writes disabled and Compass stubbed. The modification-time window is 2026-10-06 11:58:05 to 19:49:43 UTC+7. Excluded the seven specified probes and six further explicit `Run true, then reply exactly` probes: `f894483c`, `d97fd3c7`, `193cf3db`, `6102a752`, `2d85e5b9`, `3ecd744d`.

All 15 turns have `ambiguous=True`; **0/738 retained call slots have a bound output**. No captured final reply yields a pipeline-claim sentence after assertion filtering. Thus the apparent clean replay provides little evidence about supported pipeline/count precision, and no evidence of useful test/pipeline CLAIM detection on normal multi-call native turns. Six sampled sessions are hook reviews, further limiting representativeness.

### FAIL frequency and reliability

Audit snapshot SHA-256, unchanged before and after review:

```text
b5d3dbdf9fdaeeeff0229955fb60b08c49f6717a40c5f9459adab54286e926b4
```

The snapshot contains 77 physical lines, 49 outcome-tag entries and 35 distinct sessions: PASS 24, CLAIM 1, WOULD_CLAIM 13, WOULD_CASUAL 6, WOULD_SKILL 5. There are **zero FAIL and zero WOULD_FAIL entries**. Excluding all 13 probes leaves 35 outcome entries across 22 sessions, still zero FAIL. The observed FAIL share is 0/49 outcome entries, or 0/35 non-probe outcome entries. These are not independent Stop-invocation denominators: shadow tags can coexist with PASS, messages can span lines, and the log does not identify classifier attempts. A per-attempt firing rate cannot be recovered. The original brief's older 17 WOULD_FAIL events are absent from this snapshot.

Offline FAIL judgment used the actual admission guard, `has_stated_requirement`, `current_mode`, evidence assembler and `label_support`; no Jev requests were made. Two sampled turns are excluded by `current_mode`; 13 are work-shaped. Two work turns can supply local support for a hard label: `33d1187a` retains a failed exploratory read despite later successful chart/export checks, and `7dd3726a` retains a failed inspection command despite a corrected inspection and successful checks. The other 11 have no local support. These are conditional support routes, not observed Compass flags, and are not counted as natural true or false positives.

Actual FAIL precision remains **unmeasured**. Findings 4 and 5 demonstrate unsafe steering if Jev chooses a hard label. Keep FAIL shadow independently of CLAIM.

## Findings, ranked

All cases below were reproduced against HEAD `733abe7313a1dfcd6116cfd4cd3b1652c9c1fb51`. They used the real parser, claim functions and hook. Only remote classification, unrelated skill/restyle gates, logs and persistent counters were stubbed. `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM,FAIL`, or FAIL alone where specified, was confined to the replay process. A block means exit 0 with `decision: continue`; the deployed default remains shadow.

Receipt notation: `N` means the native `Created At`, `Completed At`, exit-0 and `Output:` envelope. Calls have explicit matching IDs unless a case says ID-less. Commands and outputs below are captured fixture data, not commands executed against external services. E1 through E8 identify the corresponding reproduced evidence.

### 1. P1: Native multi-call turns disable test/pipeline CLAIM, including known explicit failures

Locations: `sage/claim_receipts.py:93`, `:98`; `sage/claims.py:153`, `:186`.

E1: two sequential ID-less calls, each `true` followed immediately by its own native exit-0 result, then `dbt build passed.` produce no gap and `{}`. Replacing the second call with pytest and an explicit native `1 failed`/exit-1 result still permits `Tests pass.`. There is no overlapping planner or reversed completion in these controls.

A mixed trace with two unrelated ID-less calls plus an explicitly ID-bound failed dbt build also emits `{}` for `dbt build passed.`. Global `ambiguous=True` suppresses the known contradiction even though the dbt receipt is unambiguous. The natural sample independently shows 15/15 affected turns and 0/738 paired outputs.

Single ID-less pytest success is covered, while a single captured `true` correctly cannot cover `Tests pass.`. The loss is caused by the new whole-turn multi-call ambiguity rule.

Smallest fix: bind strictly serial native call/result windows where identity is established; abstain on genuinely overlapping ID-less windows. Carry uncertainty per call/operation instead of a global veto, so an unrelated ambiguous call cannot suppress an explicitly matched failed operation. Do not restore global FIFO across overlapping planners.

### 2. P1: Count parsing assigns columns by numeric position, causing false blocks and false equality

Locations: `sage/pipeline_receipts.py:75`, `:86`, `:88`; `sage/pipeline_claims.py:127`, `:144`.

E2: command `sqlcmd -Q "select 1 as batch_id, count(*) as source_count, count(*) as target_count"`, native exit 0, and:

```text
batch_id source_count target_count
-------- ------------ ------------
1        120          120
(1 rows affected)
```

`count_observation` returns source=1, target=120; the actual hook blocks the true `Row counts match: 120 and 120.` assertion. Changing only the data row to `120 120 119` makes it infer source=120, target=120 and emit `{}` for `Row counts match.`, accepting unequal actual counts. Labels are selected from the header, but zipped with every number from the row.

Other genuine receipt shapes also false-block: an exit-0 count query returning `[{"source_count":"120","target_count":"120"}]`, and a sqlcmd difference-count table `difference / ---------- / 0 / (1 rows affected)`. Both yield empty observations. The exact two-column equal table, labelled counts and `difference=0` controls pass.

Smallest fix: map data cells to their actual header indexes, including ignored columns. Parse supported structured count records and one-column labelled difference tables. For unrecognized schemas, mark that operation unauditable and abstain instead of inferring inequality or absence.

### 3. P1: An inspection mentioning an operation turns a successful opaque wrapper into a false block

Locations: `sage/pipeline_claims.py:188`, `:194`, `:242`.

E3: a genuine command `python3 -c "import subprocess; subprocess.run(['dbt', 'build'], check=True)"` returns native exit 0 and `Done. PASS=76 WARN=9 ERROR=0`. By itself, `dbt build passed.` abstains because the wrapper is opaque.

Add only `rg "dbt build" README.md`, with native exit 0 and the matching README line, and the real hook emits a pipeline/data CLAIM steer. Changing the inspection to `rg "usage" README.md` restores `{}`. The inspected string makes `_claim_command_match` think an explicit auditable dbt command exists; the receipt validator then cannot match the Python invocation and blocks.

Smallest fix: establish execution identity before an inspection can cancel wrapper uncertainty. Searching, displaying or quoting an operation is not running it. Keep uncertainty local to the relevant wrapped operation while preserving the already-fixed explicit dbt failure plus unrelated-wrapper control.

### 4. P1: FAIL support interprets successful dbt summaries and multiline command text as failures

Locations: `sage/jev/verdict/support.py:22`, `:55`, `:67`; `sage/jev/evidence/assemble.py:224`.

E4: successful `dbt build`, native exit 0, output `Done. PASS=76 WARN=9 ERROR=0`, reply `dbt build passed.` has no CLAIM gap, yet `label_support('claim_conflict', ...)` returns that successful line as a failure. The failure-summary regex matches **`9 ERROR`**, taking the warning count as an error count. Without WARN it matches **`76 ERROR`**. With only the hard-label choice injected, the actual FAIL-only hook emits `decision: continue` citing the success summary.

A successful multiline command containing a comment `# 1 failed reproduction`, followed by native exit 0 and `ok`, likewise steers under the injected label. `_receipt_lines` mixes command continuation lines with output; `lines[1]` is no longer the status line. This reproduces the older command-text-as-output defect that motivated shadow mode.

These prove local-support reachability, not the frequency with which Jev chooses the label.

Smallest fix: preserve command, normalized status and output as distinct structured fields through support composition. Parse dbt PASS/WARN/ERROR by field value, and match runner summaries only in output using their own grammar. A success summary cannot corroborate a conflict.

### 5. P1: FAIL support is still unrelated to the claimed operation and misses repaired rerun variants

Locations: `sage/jev/verdict/support.py:74`, `:80`, `:130`.

E5: pytest initially returns exit 1 and `1 failed`; `uv run pytest -q` then returns native exit 0 and `12 passed`; reply `Tests pass.`. CLAIM finds valid passing evidence. `label_support('claim_conflict', ...)` still cites the first failure, and the actual FAIL-only hook steers with an injected hard label. Exact same-command reruns correctly suppress it, proving the remaining gap is command identity rather than unavailable success evidence.

An unrelated failed `python -c "import absent_example"` inspection, followed by a successful dbt build and the true `dbt build passed.` assertion, also supplies conflict support and steers under the injected label. Every hard label receives the first eligible failure without checking whether it contradicts the statement it is supposed to support.

Smallest fix: bind support to the actual claim's operation, target and latest result. Recognize equivalent runner invocations only when scope is established; suppress superseded exploratory failures. A failure belonging to another operation must not corroborate `claim_conflict`.

### 6. P2: Zero-error fix now permits explicit compiler failure behind a zero-exit pipe

Location: `sage/pipeline_claims.py:223`, `:228`.

E6: `dbt compile | tail -5`, receipt `exit=0\nCompilation Error\nCould not find ref example_model`, reply `Compiled clean.` produces no gap and `{}`. A native exit-0 envelope behaves identically. The wrapper exited successfully while the compiler reported failure. Changing only the authoritative status to exit 1 blocks; `Found 0 errors.`/exit 0 still passes and positive numeric error counts block.

The removed generic failure veto was too broad for zero errors, but its replacement only checks a numeric `N errors` token. It ignores nonnumeric compiler diagnostics. This is introduced by the reviewed change.

Smallest fix: preserve explicit compiler failure-summary precedence, including `Compilation Error` and positive diagnostic codes, while exempting recognized zero-error summaries. Keep structured nonzero/error status authoritative.

### 7. P2: Status fallback combines fields from separate records

Locations: `sage/pipeline_receipts.py:46`, `:56`; `sage/pipeline_claims.py:80`, `:83`.

E7: `cloud job status target-run`, native exit 0, output:

```json
{"id":"target-run"}
{"id":"old-run","status":"Completed"}
```

Reply `Pipeline target-run completed.` is accepted with no gap and `{}`. `status_record` returns `{}` because the body is not one JSON object; independent fallbacks return `run_id=target-run` and `terminal_status=completed`. No record establishes completion of target-run. The wrong-ID single-record control correctly blocks.

Smallest fix: parse ID/status together from one record and match that record to the asserted run. When a body contains multiple or incomplete records, abstain unless a supported format can establish their association. Never synthesize a completed run by independently matching fields.

### 8. P2: Missing result capture is considered unambiguous evidence of absence

Locations: `sage/claim_receipts.py:88`, `:105`; `sage/claims.py:186`.

E8: a single ID-less `dbt build` call with no captured result gives `ambiguous=False`, an empty receipt, and a CLAIM steer for `dbt build passed.`. An explicit-ID call without its result behaves the same. This proves a missing capture can steer; it does not prove the underlying command succeeded.

The earlier safety principle was to abstain on unknown evidence. A captured unrelated `true` result is known insufficient and correctly blocks; a captured successful dbt result passes. A missing result is neither control. The parser only marks unequal positional counts ambiguous when at least one positional output exists.

Smallest fix: mark an attempted operation with an uncaptured result as unauditable for that operation. Keep blocking when complete captured evidence establishes that the asserted operation was not run or failed; do not convert an empty capture into certainty about execution.

## Safety and artifact verification

| Check | Result |
|---|---|
| No-action Q&A cannot receive CLAIM or FAIL | Confirmed on the actual hook path: `hooks/agy-stop-audit.py:196` slices at the last explicit user input; CLAIM calls with `require_action=True` at `:306`; FAIL independently checks `has_execution_or_edit` at `:308`; `sage/claims.py:195` suppresses CLAIM before scanning. Actual replays for a tool-free turn, a read-file-only turn, and the older prior-write plus `Explain how to run tests.` scenario all emit `{}` with zero Compass calls and zero steers. This protects absence of execution/edit, not every conversational turn that uses a shell. Explicit all-tag block mode can still enable unrelated SKILL/CASUAL steering. |
| fullyIdle=False | Unsupported dbt completion logs WOULD_CLAIM_BG and emits `{}`; no Compass call and no steer spent. The return at `hooks/agy-stop-audit.py:258` precedes all normal gates. |
| Cap and budgets | MAX_STEERS_PER_SESSION remains 2; the cap replay emits `{}` before gates, and sequential cap tests pass. The Compass budget remains 8 seconds. The counter is a non-atomic read/write file; simultaneous Stop invocations are not proven capped atomically. |
| Fail-open | Malformed input, missing/unreadable transcripts, import failures, sanitizer failures, gate errors and cap paths pass contract tests. Qualification remains: a caught gate exception skips only that gate. An injected CLAIM exception followed by supported configured FAIL still emits a steer. Strict whole-event `any exception -> {}` is not implemented. AGY_STOP_AUDIT=0 or AGY_SAGE_DISABLED=1 disables the hook; AGY_JEV_GATE=0 disables only Compass. |
| Actual default | `tag_should_block` defaults to an empty list at `hooks/agy-stop-audit.py:151`. No mode/tag overrides were present before or after module import. The registered agy-stop-audit Stop command has no override. CLAIM and FAIL remain shadow. |
| Claude | `hooks/claude-stop-audit.py:214` still requires CLAUDE_STOP_AUDIT_MODE=block; that file is unchanged in this commit and the current process has no override. Shared receipt/support changes affect shadow findings and coverage, while the default steering policy remains shadow. Claude tests passed in the full suite. |
| Installed copies | Hook plus all six changed shared modules match the repository byte-for-byte. Registration remains `python3 /Users/__blitzzz/.config/agy/agy-stop-audit.py`, timeout 30. |
| Sensitive additions | Inspected all eight changed files and 358 added diff lines, including fixtures. No real client names, other people's personal data, financial figures or secret values found. Added-line credential assignment, private-key, named-client and non-example-email scans returned zero matches. Fixtures use generic records and example.com. The pre-existing untracked demo-video/.env was not opened or touched. |

Independent verification:

```text
uv run pytest -q tests
742 passed, 2 skipped, 618 subtests passed in 8.49s

uv run ruff check <all eight changed Python files>
All checks passed!

git diff bcb5503..733abe7 --check
exit 0
```

No source edits, commits, pushes, configuration changes, production counter/log writes, live agy sessions or messages to others were made. Only requested report outputs and disposable offline scratch data were written. Repository status remains the pre-existing untracked `.env`.

## Bounded recurrence sweep and limits

Snapshot: `review733-20261006`, source revision 733abe7, installed-byte check above, natural transcript modification-time window above. The bounded mechanism inventory is U1 through U8, corresponding to the eight ranked findings; H1 through H8 are their negative defect propositions. R1 through R8 test the respective unit/hypothesis pairs with the same real hook and normalized transcript path. Impacts are high for R1 through R5 because false steers or widespread detection loss waste work; medium for R6 through R8 because narrower receipt failures remain. Root causes are confirmed with high confidence inside these reproduced cases. Propagation is the shared sage modules and installed copy synchronization, distinct from parsing/identity defects.

Universe completeness is **unknown** beyond this declared sweep: neither the 15-session convenience sample nor the tested output shapes exhaust native producers, command formats or classifier outcomes. No class-wide safety claim is made.

| Axis or interaction | Disposition | Scope/evidence |
|---|---|---|
| Instances | Expanded | Natural U1/R1 snapshot: 15 final turns, 738 retained call slots. |
| Inputs | Expanded | Native envelopes, structured flags, single JSON/dict, multi-record JSON and sqlcmd/JSON counts in R2/R4/R7/R8. |
| Variants | Expanded | Vietnamese pytest/dbt routing, test/build/compile/count controls, mode selection and R3/R6. |
| Siblings | Expanded, with gap G1 | Installed hook/modules and Claude shadow policy verified; behavioral precision of other shared-module consumers is not enumerated. |
| Error family | Expanded | False blocks and false acceptance examined separately in R1 through R8. |
| Boundary | Expanded | Missing/duplicate IDs, missing outputs, multiline/ANSI summaries, extra columns, zero/nonzero flags and R2/R4/R7/R8. |
| Regression | Expanded | Full suite, earlier exact reproductions and their opposite controls; R1/R3/R6 reveal new regressions. |
| Input x ordering | Expanded | ID-less overlap, strict serial windows, delayed ID-bound results and mixed explicit/ambiguous calls in R1. |
| Wrapper x inspection | Expanded | R3's wrapper-only, named-inspection and unrelated-inspection counterfactuals. |
| Label x receipt | Expanded, with gap G2 | R4/R5 exercise actual local support with injected labels. Natural classifier precision remains unverified because no FAIL events or per-attempt records exist. |

| Result | Unit | Negative hypothesis | Assertion/evidence | Status | Mix and aggregate | Blind validation |
|---|---|---|---|---|---|---|
| R1 | U1 | Native ambiguity disables applicable test/pipeline CLAIM checks | Known unexecuted/failed claims allowed; E1 | Confirmed | 1/0/0, affected | Required high-impact check not performed |
| R2 | U2 | Count receipt fields are misattributed or missed | Equal counts block; unequal counts pass; E2 | Confirmed | 1/0/0, affected | Required high-impact check not performed |
| R3 | U3 | Inspection text cancels justified uncertainty | Adding only named inspection causes block; E3 | Confirmed | 1/0/0, affected | Required high-impact check not performed |
| R4 | U4 | Nonfailure content corroborates FAIL | Success summary/command text yields steer; E4 | Confirmed | 1/0/0, affected | Required high-impact check not performed |
| R5 | U5 | Superseded/unrelated failures corroborate a claim conflict | Valid latest success still steers; E5 | Confirmed | 1/0/0, affected | Required high-impact check not performed |
| R6 | U6 | Compiler failure is accepted as success | Explicit compilation error allowed; E6 | Confirmed | 1/0/0, affected | Not required at frozen medium impact |
| R7 | U7 | Status and ID are borrowed across records | Incomplete target record accepted; E7 | Confirmed | 1/0/0, affected | Not required at frozen medium impact |
| R8 | U8 | Missing capture establishes absence | Uncaptured attempted result causes steer; E8 | Confirmed | 1/0/0, affected | Not required at frozen medium impact |

Mix means confirmed/clear/unverified result counts. Rollup: |U|=8, |R|=8; confirmed 8, clear 0, unverified 0; affected units 8, clear 0, unresolved 0. Decisive primary coverage is 8/8 of this enumerated mechanism snapshot. G1/G2 are unenumerated completeness gaps, not extra result rows. Detection evidence is the reproduced assertion failure in E1 through E8; separate controls are described with each finding. A final bounded pass through prior reproductions, producer formats and the safety paths added no further ranked mechanism.

Independent blind validation was not performed, consistent with the task's no-messages boundary. Required high-impact validation and unknown universe completeness therefore remain blockers to a class-clear or safe-to-enable claim. The implementer's earlier review reports are not treated as independent proof of this new evidence.

Recommended containment: retain the existing empty default block-tag list. The hook maintainer should fix execution identity and receipt parsing at the shared source, add these native-envelope regression controls as preventive merge checks, and keep per-stop classifier/label/support diagnostics as a detective control. Run shadow observations with adjudicated positive and negative pipeline/count cases before considering CLAIM, and evaluate FAIL separately. No historical data backfill is needed because this review changes no artifacts or data. Rollout should remain fail open on unknown capture; rollback for excessive false steers is AGY_STOP_AUDIT_MODE=shadow. This report recommends that path without changing configuration. Residual risk is unsupported reporting during shadow mode and unmeasured classifier precision.
