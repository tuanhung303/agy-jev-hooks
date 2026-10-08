# Deferral check: checkpoint deferral-v1

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
3. Jev answers `d_defer` plus 8 risk questions (`x_question`, `x_gate`, `x_external`, `x_irreversible`,
   `x_unauthorized`, `x_user_only`, `x_guess`, `x_costly`). The verdict is `continue` only when
   `d_defer > 0.5` and every risk is below 0.5.

Private names (client systems, frontend host) live in `~/.config/agy/deferral.json`, never in the repo.

## Modes and switches

- Shadow by default. Each checked stop logs a `deferral` record. The decision is `would_block:deferral` when the verdict is `continue`.
- `CLAUDE_DEFERRAL_MODE=block` steers once per stop, up to `MAX_STEERS_PER_GATE` per session. The steer quotes the offer and says "do it now, or name the gate". This flag is independent of `CLAUDE_STOP_AUDIT_MODE`.
- `CLAUDE_DEFERRAL_CHECK=0` turns the check off.

## Baseline at the checkpoint

The numbers are in `baseline.json`.

- Held-out set: 100 real cases with blind labels. The check scored precision 0.83 and recall 0.43, with 2 false continues out of 77 holds.
- The block bar is precision 0.85 or higher on a fresh held-out set, so the check stays in shadow mode.

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
