# Deferral check: checkpoints deferral-v1, v2 and v3

## What it does

A Stop-time check for replies that hand an action back to the user ("want me to commit?").
When the user's own rules say the agent should have just done it, the check returns `continue`.
Otherwise it returns `hold`.

Pipeline (`sage/deferral.py`, case `deferral` in `sage/jev/jev.yaml`):

1. Prefilter: a regex on the last 700 characters of the reply. No match means no check and no Jev call.
2. Hard holds. These are deterministic, and any one of them forces `hold`:
   - `user_wait`: the latest request says wait.
   - `job_running`: the reply names a running job.
   - `file_approval`: an approval claim was read from a file.
   - `confirm_gate`: a start-of-task "run it" confirmation.
   - `message`: the deferred sentence sends a message.
   - `client_system`: a client system named in the private config.
   - `weekday_publish`: a production publish on Monday to Friday.
3. Jev answers one `branch` choice question and four boolean vetoes (`x_external`, `x_irreversible`,
   `x_guess`, `x_costly`). The `branch` question carries an ordered if/else tree as YAML text. Jev walks it
   top to bottom and names the first branch the offer reaches (for example `gate_message`, `user_decision`,
   `finish_own`, `next_step`). The verdict is `continue` only when the continue branches (`finish_own`,
   `next_step`, `plain_fix`, `cheap_read`) hold more than 0.5 of the probability and every veto is below 0.5.

Why v3 changed shape: v1 and v2 asked 8 independent risk questions, and any one of them could hold the case.
Each misfired a little, so on a fresh set they held 24 of 28 true continues. The judgment calls (was it a
question, is it the user's decision, was it asked for) now sit in one ordered tree. The vetoes keep only
the objective risks.

Since deferral-v2 the gates are read from the block between `<!-- gates:start -->` and `<!-- gates:end -->`
in `~/.claude/CLAUDE.md` (override with `AGY_GATES_SOURCE`). The rules file is the single source, and the
hook keeps no copy. A built-in fallback applies only when the block is missing, and the record's `rules`
field says which source was used. The request is split into `goal`, `policy`, `case` (list fields) and
`offer` (the exact deferred action), and each question names the field it judges.

Private names (the client-system list) live in `~/.config/agy/deferral.json`, never in the repo.

## Modes and switches

- Shadow by default. Each checked stop logs a `deferral` record. The decision is `would_block:deferral` when the verdict is `continue`.
- `CLAUDE_DEFERRAL_MODE=block` steers once per stop, up to `MAX_STEERS_PER_GATE` per session. The steer quotes the offer and says "do it now, or name the gate". This flag is independent of `CLAUDE_STOP_AUDIT_MODE`.
- `CLAUDE_DEFERRAL_CHECK=0` turns the check off.

## Baseline at the checkpoint

The numbers are in `baseline.json`.

- Fresh set (deferral-v3): 100 real cases from 32 sessions, blind labels, 28 expected continues. Two runs gave
  precision 1.00 and 0.91, recall 0.36. v2 scored 0.67 / 0.14 on the same set.
- The block bar (precision 0.85 or higher on a fresh set) is met on the point estimate, but only 10 or 11
  continues back it. The check stays in shadow mode until live data agrees.

## Measuring the change later

Run:

```sh
scripts/deferral_delta.py
```

It reports the following since the checkpoint time:
- stops;
- the check rate;
- verdicts;
- hard holds;
- latency;
- a proxy outcome for each `continue`, taken from the user's next message (a go-word, a stop-word, or other).

To re-score offline against the same labelled sets, run `replay.py` in the eval-data folder named in `baseline.json`.
