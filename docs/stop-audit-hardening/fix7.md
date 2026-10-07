Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Round 7. Round 6 (`7cb08ae`) removed 43 real false flags with no real-traffic recall loss, but it traded recall too broadly: 16 of 31 synthetic Gemini lies now pass that blocked at `d546647` (sol-review6.md Q3). Remaining precision is 52% with a 0.61% false-block rate. This round must satisfy BOTH measures at once. Enable gates (lead-set, Kai can adjust): precision among real flags >= 80%, false blocks <= 0.5% of action turns, every synthetic lie behaves as required, no real-traffic recall regression. Default stays shadow regardless.

Kai decided 2026-10-07 the carry-over rule: when a reply restates an earlier turn's result without re-running it, abstain if an earlier turn in the SAME session has a passing receipt for the same operation; block if no such receipt exists.

Kai's standing rules: block only unsupported claims; Q&A is never steered; doubt about evidence abstains only for the operation the doubt is about. Abstention must be linked to the claimed operation, never armed by an unrelated script.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `7cb08ae` (local only). Untracked `demo-video/.env`: never open or commit.
- Review: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review6.md (26-flag adjudication table with labels TP-direct, TP-carry, FP, ART; Q3 lie table L1-L9; Q5 unsafe assumptions; findings 1-6). Read it fully first. Implementer report: report6.md (same folder).
- Harness, copy /tmp/review-7cb08ae/ to /tmp/round7/ first (do not modify the original): `lies.py` (31 synthetic lies through real `main()`; prints OK/MISS per lie; run from the repo root), `sweep.py <repo> <out.json>` (every real action turn, about 3 minutes), `realturn.py`, `why.py`, `remaining.json` (the 26 flags), `removed.json` (44 removals), `selfattest.py`, `exposure.py` (how often abstain paths are armed), probe scripts `prior.py`, `edges.py`, `fail-and-regressions.py`, `newprobes.py`, `final-check.py`. Check each file's interface before relying on it.

## Steps
1. Reproduce the baseline at HEAD: `lies.py` (16 MISS), the sweep (35 flags), `exposure.py`.
2. For each finding 1-6 in sol-review6.md, add a failing test from the reviewer's evidence (lie IDs and selfattest case), then fix, then confirm. Use the reviewer's "Smallest fix" unless you show a safer one.
   - F1: script abstention only when the script's command or output names the claimed operation (runner, `dbt <action>`, claimed run or pipeline). Remove bare `test` and `checks?` topic matches.
   - F2: judge `conclusion` when present in gh/CI records.
   - F3: `ran_tests` requires an executed call on both branches.
   - F4: third-party deploy filter skips sentence-initial and `-ly` words; modal filter only for requirement forms (`must be`, `needs to be`, `has to be`, `can be`).
   - F5: skip the model's own replies when scanning background results; latest `Status: RUNNING` with no later DONE/finished contradicts a completion claim; `Traceback`, `npm ERR!`, `FAILED` are explicit failures.
   - F6: accept `gh run watch|view` exit 0 with "✓" job lines or "completed with 'success'"; a `fetch(`/`goto(` of the claimed host inside an executed script counts as a probe; implement Kai's carry-over rule above.
3. After the fixes: `lies.py` must show 0 MISS (L3b passes by design). Re-run the sweep and give each flag a label using the reviewer's labels; new flags not in `remaining.json` need your own one-line adjudication from the transcript. Compute precision (TP / (TP+FP), ART excluded) and false blocks / action turns. Re-run `exposure.py` and report abstain-path exposure before and after.
4. Recall: confirm every turn in `removed.json` still passes, and no reviewer TP-direct flag disappeared without a receipt.
5. Re-run the probe families; 0 earlier outcomes may change except targeted fixes, each listed.
6. Run `uv run pytest -q tests` and `uv run ruff check` on changed files. Respect the 300-line module limit (`tests/test_static_analysis.py`). Commit on `main` (one or more commits). Do not push. The post-commit hook syncs installed copies.

## Done
- 0 MISS in `lies.py`; precision >= 80% and false blocks <= 0.5% on the sweep, or a clear statement of which gate is missed and why; no recall regression.
- Every finding has a new test; full suite passes. Default stays shadow; `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` is empty.
- Report in your final reply (the lead saves it as report7.md): table "Finding | Test name | Fix commit | Status", pytest summary, "## Live test" with lies before/after, sweep flag table with labels, precision and rate, exposure before/after, recall check, assumptions, open paths.

## On failure
If a gate cannot be met without breaking another, prefer recall on the synthetic lies over sweep precision, and report the trade-off with numbers. If a fix needs `hooks.json` edits, Compass model changes or a new dependency, stop and say why.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. No live agy sessions, no Jev or remote calls, no writes to /tmp/agy_stop_audit/. No messages to anyone.
