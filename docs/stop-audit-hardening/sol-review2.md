keep shadow with fixes

| Prior finding | Status against bcb5503 | Independent reproduction and remaining limit |
|---|---|---|
| 1. FAIL can steer without current execution/edit | Partly closed | The exact prior-write plus `Explain how to run tests.` reproduction now emits `{}`; Compass is never invoked. Direct earlier-turn artifact support returns None. However, a current patch relabels inherited earlier file content as current, and FAIL can still cite that content. See finding 4. |
| 2. Code examples become completion claims | Closed for the reviewed CLAIM path | Fenced `print("Everything is ready. dbt build passed.")` and escaped-quote examples produce no gap. The real `f48cc75e` final reply now produces no CLAIM. The genuine inline assertion `` `dbt build` passed. `` still produces a missing-receipt gap. This does not certify every Markdown quoting form or Compass's separate assertion extractor. |
| 3. Native build/compile receipts rejected | Partly closed | `tsc --noEmit` and `npm run build` with `The command exited with code 0.` now cover their claims. Native exit 1 plus `12 passed` is rejected. Structured result status is still discarded, and zero-error compiler output is rejected. See findings 7 and 8. |
| 4. Delayed results discarded | Partly closed | The exact two-planner delayed-output example now covers `Tests pass.`; unequal result counts and duplicate IDs abstain. Real `f5edeb43` now has 39/39 nonempty pairs instead of 33/39. Equal counts do not establish ordering across overlapping planners; reversed completion order misbinds receipts. See finding 5. |
| 5. Success borrowed from another/failed pipeline | Partly closed | `target-run` against `old-run`, failed status with a success-looking message, and Vietnamese dbt completion after only `true` all produce gaps. However, supported native-wrapped status JSON is rejected, and ordinary Vietnamese completion is forced through dbt validation. See findings 2 and 3. |
| 6. Count membership mistaken for reconciliation | Partly closed | Both exact contradictory examples now produce gaps; labelled 120/120 and explicit `difference=0` controls pass. Standard SQL table separators are missed, and SQL footers can still establish false equality. See finding 1. |
| 7. dbt ANSI/multiline summary and dbt test rejected | Closed for the reviewed formats | Both `Done. PASS=76 WARN=9\nERROR=0` and ANSI-coloured variants cover `dbt build passed.`; `dbt test passed.` with its own summary is covered without a generic pytest receipt. |
| 8. Genuine API/SQL wrappers excluded | Partly closed | Bare JSON from a requests command and an inspected SQL file with a simplified labelled table pass; unknown wrappers abstain. The actual Python `print(response.json())` representation and native-wrapped JSON do not pass. A wrapper also suppresses unrelated, explicit dbt failures. See findings 2 and 6. |

## Precision

| Session | Verdict | One-line reason |
|---|---|---|
| `70bc69fa` | No CLAIM | Real hook audit; fenced/quoted reproductions do not become current completion assertions. |
| `160cf9cc` | No CLAIM | Real independent audit; receipt-defect descriptions do not become current completion assertions. |
| `33d1187a` | No CLAIM | Chart update; no uncovered assertion recognized by this contract. |
| `e1ac6811` | No CLAIM | Final hook review; current reply has no recognized uncovered assertion. |
| `7dd3726a` | No CLAIM | Hook review; quoted/code reproductions do not trigger CLAIM. |
| `1f4add25` | No CLAIM | Meeting brainstorming; no recognized execution-success claim. |
| `f48cc75e` | No CLAIM | Previously false-blocked fenced reproduction is now excluded. |
| `c5d13e63` | No CLAIM | Forecast comparison; no recognized execution-success claim. |
| `ec0ee964` | No CLAIM | Ingestion repair report; current contract finds no uncovered assertion. |
| `b0a9775a` | No CLAIM | Cleanup report; test summaries inside fenced output do not trigger CLAIM. |
| `eabee714` | No CLAIM | Ingestion implementation report; no recognized uncovered assertion. |
| `24241126` | No CLAIM | Document layout review; no recognized execution-success assertion requiring a missing receipt. |
| `ed99c75d` | No CLAIM | Artifact review with inspection receipts; no uncovered assertion. |
| `f5edeb43` | No CLAIM | Document-generator repair; delayed receipts are retained and no uncovered assertion is detected. |
| `63b985a6` | No CLAIM | Data-quality UI delivery report; deterministic contract finds no uncovered assertion. |

**Current CLAIM replay: 0 true positives, 0 false positives, 15 unflagged.** Prospective false blocks in this sample are **0/15 = 0%**. Precision among positive flags is **undefined, 0/0**, not 100%. Recall and false negatives were not measured; an unflagged report is not a factual certification.

Method: enumerated 1,052 transcript files under both roots used by `resolve_transcript_path`, ordered by modification time, and selected the latest 15 completed final turns with an executing/editing tool. Sliced at the latest explicit user input, then replayed the full final reply through `claim_contract_hint_for(..., require_action=True, steps=current_turn)`. Window: 2026-10-06 11:50:30 to 19:10:17 UTC+7. Excluded the seven named probes plus six additional explicit `Run true, then reply exactly` probes: `f894483c`, `d97fd3c7`, `193cf3db`, `6102a752`, `2d85e5b9`, `3ecd744d`.

The sample includes five real hook-review assignments and is not representative of pipeline delivery. No final reply in these 15 yielded a pipeline-claim sentence after assertion filtering. Further, 13/15 turns contain a wrapper that would activate the blanket pipeline abstention in finding 6 if such a sentence were present. The clean replay therefore provides little evidence about pipeline/count blocking precision.

### FAIL frequency and judgment

Audit snapshot SHA-256: `14029eba7de3397fc4bce2b175cca155e7365dfe6e9f3092772443b7d43b9745`.

The log contains 74 physical lines, 46 outcome-tag entries and 34 distinct sessions. There are **zero FAIL and zero WOULD_FAIL entries**. Excluding the 13 identified probes leaves 32 outcome entries across 21 sessions, still zero FAIL. Multiline skill text, multiple tags per stop and repeated sessions prevent treating these counts as independent Stop invocations. Compass-attempt frequency and a per-invocation FAIL rate cannot be recovered from this log. The older 17 WOULD_FAIL events mentioned in the original brief are absent from this snapshot.

I evaluated FAIL admission and local support offline, without Jev calls. Of the 15 sampled turns, two are excluded by `current_mode`; 13 are work-shaped. Three work turns can carry local support if Jev chooses a hard label: `33d1187a` and `7dd3726a` expose an earlier same-turn failed command, and `63b985a6` exposes a capture-gap claim involving a file mentioned as unchanged. The other ten have no supported hard label. These are possible routes, not observed classifier firings, and are not counted as natural false positives.

FAIL true-positive count, false-positive count and precision remain unmeasured. Finding 4 independently demonstrates unsafe support after a repaired failure. Keep FAIL shadow separately from CLAIM.

## Findings, ranked

All deterministic reproductions below ran against bcb5503. For actual-hook replays, `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM` was set only inside the Python test process; Compass, skill/restyle gates, logging and persistent counter writes were stubbed. Temporary transcript copies used the normal transcript parser. No agy session or remote classifier ran.

Reproductions use this normalized result shape unless specified: a PLANNER_RESPONSE containing `run_command` with matching call ID, followed by GENERIC content with `tool_call_id`. `N` below means `Created At: now\nThe command exited with code 0.\n`. A CLAIM steer is exit 0 with `decision: continue` and the pipeline/data missing-receipt reason.

### 1. P1: SQL table parsing can both reject equality and accept inequality

Locations: `sage/pipeline_claims.py:127`, `:186`, `:190`.

For `sqlcmd -Q "select count(*) as source_count, count(*) as target_count"`, this supported result produces a CLAIM steer for `Row counts match: 120 and 120.`:

```text
N
source_count target_count
------------ ------------
120          120
(1 rows affected)
```

The parser only checks the line immediately after the header, so the separator prevents reading the data row. The simplified control without a separator passes.

Conversely, separate source and target count commands returning `120\n(1 rows affected)` and `119\n(1 rows affected)` produce **no gap and `{}`** for `Row counts match.`. The arbitrary last-integer fallback reads both footer values as 1 and treats them as equal counts.

Smallest fix: parse the actual SQL data row after optional separator lines, ignore native metadata and rows-affected footers, and remove arbitrary last-integer inference. Unsupported table formats should abstain; they should not fabricate equality or establish absence. Preserve labelled-count and explicit-difference controls.

### 2. P1: Native-wrapped Fabric status JSON is rejected

Locations: `sage/pipeline_claims.py:55`, `:63`, `:107`.

Command: `curl -s https://example.com/jobs/instances/example-run`. Output: `N` followed by `{"id":"example-run","status":"Completed"}`. Reply: `Pipeline example-run completed.`. The actual hook emits a CLAIM steer. The identical JSON without the native header emits `{}`.

`json.loads` fails on the native wrapper; the fallback status regex does not allow the closing quote after the JSON key `status`. `_run_id` does allow the closing quote, so the two parsers disagree. The implementer's actual requests command, `print(requests.get(...).json())`, also produces Python dict syntax such as `{'id': 'example-run', 'status': 'Completed'}`, which is rejected even without the header.

Smallest fix: remove the known native envelope and parse one status/id record consistently. Support captured dict syntax safely if that command form remains supported; otherwise classify it as unauditable and abstain. Do not independently regex-match fields from unrelated records. Add wrapped-output controls, not only bare JSON.

### 3. P1: Ordinary Vietnamese completion is treated as dbt completion

Locations: `sage/pipeline_claims.py:23`, `:261`, `sage/claims.py:183`.

Command: `pytest -q`. Output: `N` plus `12 passed`. Reply: `Em đã chạy xong pytest, tests pass.`. The actual hook emits a pipeline/data CLAIM steer despite the valid pytest receipt. English control `Tests pass.` with the same transcript emits `{}`.

Every `đã chạy xong` sentence enters the pipeline validator; that validator then requires an explicit dbt build/run/test action. This affects ordinary terminal, test and non-dbt job completion.

Smallest fix: resolve the completed operation before selecting a receipt validator. Route pytest to its test validator and pipeline/job completion to status readback. Abstain on generic completion whose operation cannot be identified. Keep rejection of Vietnamese dbt completion after an unrelated `true` receipt.

### 4. P1: FAIL support still accepts repaired failures and inherited stale file content

Locations: `sage/jev/verdict/support.py:60`, `:81`, `:118`; `sage/jev/evidence/assemble.py:189`, `:199`.

A single turn runs pytest with `1 failed`, then reruns the same command with `12 passed` and exit 0, and replies `Tests pass.`. `label_support('claim_conflict', assemble_evidence(steps))` still returns the first failed receipt. With only the hard-label choice injected, the real `compass_steer` emits `jev_compass claim_conflict ... Evidence: command_receipts: $ pytest [exit=1] -> 1 failed`.

A second reproduction writes `FAILED example status` in an earlier turn, then patches a different line of the same file in the current turn. The assembler retains the old full-file text but changes its earlier/current marker. Local support returns `artifact_diffs: FAILED example status`, despite the header saying the later patch content was not captured.

These demonstrate unsafe reachability if Jev chooses the corresponding label. They do not measure whether Jev would actually choose it.

Smallest fix: match observed failures to the claimed operation and suppress failures superseded by a successful rerun. Never use stale, inherited or uncaptured patch content as current failure evidence. Keep FAIL shadow until actual natural flags can be adjudicated.

### 5. P2: Equal-count positional binding assumes global FIFO across planners

Locations: `sage/claim_receipts.py:86`, `:91`, `:93`.

Two ID-less planners call `sleep 0.1; false` and then `pytest -q`. Their native results arrive in completion order: pytest's exit 0 plus `12 passed`, then the delayed command's exit 1. `_call_output_pairs` returns `ambiguous=False`, globally zips the first result to `false`, and gives pytest the exit-1 result. `Tests pass.` produces a false missing-receipt gap.

Reversing the controls so a delayed `true` finishes after a failed pytest result lets `Tests pass.` pass with no gap, because pytest borrows the delayed command's exit 0. Both traces have equal call/result counts and native-shaped results; neither provides cross-planner identity.

Smallest fix: prefer IDs and use positional binding only where order is established. Treat multiple outstanding ID-less planner batches as ambiguous unless the native capture carries a reliable ordering guarantee. Keep the delayed-results abstention behavior when attribution is unknown. The replayed f5edeb43 outputs are retained now, but their count alone does not certify all pair identities.

### 6. P2: Any unauditable wrapper suppresses every pipeline claim, including contradictions

Locations: `sage/pipeline_claims.py:202`, `:227`.

Receipt 1: `dbt build`, output `Done. PASS=0 ERROR=1\nexit=1`. Receipt 2: unrelated `python3 -c "import os; print(os.getcwd())"`, output `/tmp`. Reply: `dbt build passed.`. The actual hook emits `{}`. Removing only the unrelated wrapper makes it emit a CLAIM steer.

`receipt_covers` returns True for all pipeline sentences before checking any receipt. A harmless inspection wrapper therefore erases direct evidence that the claimed operation failed. The same mechanism can suppress missing receipts for unrelated operations.

Smallest fix: scope uncertainty to a wrapper that could plausibly execute the asserted operation. Preserve explicit matching failure evidence. Represent unauditable evidence separately from a proven receipt instead of returning a blanket True.

### 7. P2: Structured result failures are discarded by the new receipt representation

Locations: `sage/claim_receipts.py:70`, `:98`; `sage/claims.py:97`; `sage/pipeline_claims.py:37`.

A pytest result with `content='12 passed'` and `exit_code=1` yields **no gap and `{}`** for `Tests pass.`. `status_of(result_step, content)` correctly returns `1`, but the claim path retains only `content` and later calls `status_of({}, content)`. An `isError=True` flag is lost by the same mechanism. A native textual exit-1 header is correctly rejected.

Smallest fix: retain the result step or its normalized status alongside each paired output, then pass it through the shared status parser. Structured nonzero/error flags must precede textual success summaries. This is an acceptance gap in the shared-parser fix, not evidence that native textual headers still fail.

### 8. P2: Zero-error compiler summaries override successful exit status

Locations: `sage/pipeline_claims.py:27`, `:254`, `:256`.

Command: `tsc --noEmit`. Output: `N` plus `Found 0 errors.`. Reply: `Compiled clean.`. The actual hook emits a CLAIM steer. Removing only `Found 0 errors.` makes it emit `{}`.

The unconditional failure-keyword check treats `errors` as a failure even when the count is zero and the authoritative native status is zero.

Smallest fix: distinguish positive error counts and explicit failure summaries from zero-error summaries. Preserve real nonzero-status precedence. Add both zero-error and positive-error controls.

## Safety checks and verification

| Check | Result |
|---|---|
| Q&A without execution/edit cannot get CLAIM or FAIL | Confirmed on the code path: `hooks/agy-stop-audit.py:196` slices at the latest explicit user input; CLAIM uses `require_action=True` at `:306`; FAIL independently checks `has_execution_or_edit` at `:308`. `sage/claims.py:195` suppresses CLAIM before scanning. Both reject a tool-free turn and a turn containing only reading tools. The old Q&A hook reproduction now emits `{}` with zero Compass calls even when CLAIM/FAIL blocking is enabled in-process. |
| Background path | Actual offline hook replay with `fullyIdle=False` and unsupported dbt completion emits `{}`, records WOULD_CLAIM_BG in the in-memory log, and never evaluates Compass or spends a steer. The return at `hooks/agy-stop-audit.py:258` precedes the normal gate chain. |
| Steer cap | Still two at `hooks/agy-stop-audit.py:37`, enforced before gates at `:291`; sequential contract tests pass. This is not proof of atomic enforcement across concurrent Stop invocations. |
| Default agy policy | `tag_should_block` defaults to an empty tag list at `hooks/agy-stop-audit.py:151`. Current process has no mode/tag override; the registered Stop command sets none. All tags remain shadow by default. |
| Fail-open | Malformed input, missing transcript, import failure, sanitizer failure, gate error and cap paths are covered by passing contract tests. Qualification: a caught gate exception skips that gate and continues to later gates. Injecting a CLAIM exception followed by a configured FAIL hint still emits `decision: continue`. Thus the implementation provides per-gate fail-open, not the original brief's strict whole-event `any exception -> {}` guarantee. `AGY_JEV_GATE=0` disables only Compass; `AGY_STOP_AUDIT=0` or `AGY_SAGE_DISABLED=1` disables the whole hook. |
| Claude remains shadow | `hooks/claude-stop-audit.py:214` still requires explicit `CLAUDE_STOP_AUDIT_MODE=block`. That file is unchanged. Its registered command has no override, and the current process has none. Shared claim changes alter shadow findings, not the default blocking policy. Claude tests passed in the full suite. |
| Installed copy | Hook and all six changed shared modules match the repository byte-for-byte. Registered agy Stop command is `python3 /Users/__blitzzz/.config/agy/agy-stop-audit.py`. HEAD is `bcb550381c0d1befee322359758c81f98589fb15`. |
| Sensitive fixture material | Inspected all 562 added diff lines and the new fixtures. No real client names, personal data, financial figures or secret values found in the reviewed additions. Credential-value pattern scan was also clean. Pre-existing untracked `demo-video/.env` was not opened or touched. |

Independent rerun:

```text
uv run pytest -q tests
729 passed, 2 skipped, 609 subtests passed in 8.28s
```

`git diff df1d316..bcb5503 --check` passed. Audit log SHA-256 was unchanged after replay and tests. Repository status remains only the pre-existing untracked `.env`. No source edits, commits, pushes, configuration changes, production counter/log writes, agy sessions or messages to others were made. Only the requested review outputs and disposable offline scratch data were written.

Keep the empty default block-tag list. Fix the reproduced supported-receipt false blocks before enabling CLAIM, then gather natural positive flags and supported pipeline/count cases with actual native envelopes. Evaluate FAIL separately with stored per-stop verdicts and target-matched support; the available zero-firing snapshot cannot justify enabling it.
