# Jev Compass bench

Replays audited Claude stops through the Compass stop gate
(`hooks/claude-stop-audit.py` → `sage.jev.verdict.compass`) so a change to the
evidence or the prompt can be compared with what ships. It reports the steer
the hook would send, not only the labels Jev fires, because local support
(`sage/jev/verdict/support.py`) drops a label it cannot back with a receipt.

```sh
.venv/bin/python bench/jev_compass/run.py --label my-change --runs 2
.venv/bin/python bench/jev_compass/run.py --label probe --only f04,pair-tdd-ordered-but-skipped --runs 3
```

About 1 minute per run (6 calls in parallel, the live 8 s budget per call).

## Cases

- **Real stops** live in `~/.local/state/agy-jev-hooks/bench/compass/`
  (`cases.json` plus one transcript per case, cut at the audited stop). They
  hold client work, so they stay out of the repository. Each has a hand label:
  `pass` (a steer there is a false alarm), `fail` (a steer is wanted) or
  `unsure` (reported, not scored). The set: 24 stops the shadow log marked
  `would_block:compass`, and 12 working turns it let pass, sampled at random.
- **Pairs** are the synthetic cases in `sage/jev/jev.yaml` (`routing.pairs`),
  written as Claude transcripts. A pair counts as right when every expected
  hard label fires and no `expect_absent` label does. Pairs carry no receipts
  that local support can quote, so they are scored on labels, not steers.

The real set has no `fail` case yet: it measures false alarms, while recall
rests on the 11 pairs. Add a real stop that deserved a steer when the shadow
log shows one.

## Reading the numbers

- **false alarms**: `pass` cases that would have been steered.
- **pairs**: synthetic cases with the right labels.
- Jev is not deterministic: a case near a 0.75 floor flips between runs, so
  compare over 2-3 runs.

## History (2026-09-29)

| Change | false alarms | pairs | latency p50 / p90 |
|---|---|---|---|
| shipped: one flat evidence dump in `criterion`, whole-session receipts | 20/35, 20/35 | 10/11, 10/11 | 2.1 s / 5.1 s |
| + judge the latest turn: Q&A turns skipped, earlier receipts split off, `pass: True` lines not failures | 9/35, 8/35 | 10/11, 10/11 | |
| + one state field per kind of evidence, tagged requests, work-in-progress and expected-failure rules | 1/35, 2/35 | 9/11, 10/11 (tdd lost) | |
| + `this_turn_order` timeline of writes and runs | 4/35, 3/35, 3/35 | 11/11 ×3 | |
| + background task notifications (Monitor events) count as receipts | 2/35, 1/35, 2/35 | 11/11 ×3 | |
| + quoted status literals in written code are not failures (this change) | 1/35 ×5 | 11/11 ×5 | 1.2 s / 1.8 s |

What moved the numbers, in order of effect:

1. Scoping to the latest turn. Receipts from earlier turns were read as
   failures of the current one, and a question asked mid-session was judged as
   a work order.
2. Named fields in place of one dump. The shipped prompt sent the request as
   "short user messages: ok / go ahead", a `T-1..T-14: user request` list and
   an old screenshot's OCR, so Jev judged interim status replies ("run 2 is
   going") as unfinished work.
3. The timeline. With commands and file writes in separate fields, Jev lost
   the write-then-test order that `tdd_breach` needs (0.66-0.71, below the
   0.75 floor); the timeline put it back at 0.86.
4. Monitor events. A reply such as "step 3 deployed" rests on a background
   task notification, which was never a receipt.

The remaining false alarm is a turn whose data build had failing uniqueness
tests while a second build ran; it is close to a real catch.
