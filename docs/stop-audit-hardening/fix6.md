Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Round 6. Round 5 (`d546647`) closed the round-4 findings. Review 5 (sol-review5.md, by an independent Claude reviewer because Codex is unavailable) then replayed every real tool-using agy turn, not only final turns: 1,439 turns, 65 real CLAIM flags, at least 23 of them false (35% of flags, 1.6% of action turns). These false blocks predate round 5. The lead reproduced three through the real hook (`aa24dd5f`:35, `cf60f761`:0, `b70b1a2d`:37). This is the first real-traffic evidence, so this round is judged on real turns, not only on synthetic cases. Default stays shadow. Kai's rules: block only unsupported claims; Q&A is never steered; doubt about evidence means abstain.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `d546647` (local only, not pushed). Untracked `demo-video/.env`: never open or commit.
- Review: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review5.md ("Wider natural replay" and "New findings, ranked" 1-7). Read it fully first.
- Reviewer scratch (read-only, copy before use): /tmp/review-d546647/ — `sweep.py` (real CLAIM gate on every explicit user turn with an execution or edit across all transcripts), `realturn.py` (one real turn through the real `main()`, usage `uv run python realturn.py <sid>:<turn> ...` from the repo root), `adjud.json` / `adjud.py` / `adjud2.py` (adjudication), `turns/` (truncated real turns), `sweep-base.json`, `sweep-new.json`. Check each file's format before relying on it.
- Earlier briefs and reports: same folder (fix5.md, report5.md) and docs/stop-audit-hardening/ in the repo.

## Steps
1. Copy /tmp/review-d546647/ to /tmp/round6/ and run `sweep.py` and `realturn.py` against HEAD to reproduce the baseline: the 65 real flags and the false positives listed in sol-review5.md. Save the baseline list of flagged (sid, turn) pairs.
2. For each finding 1-7, first add a failing test from the reviewer's synthetic evidence (D1, D3, E1, E2, G2, G4, A1-A4, A6, B1, B3, and the three non-claim texts anonymized), then fix, then confirm.
3. Finding 1 (P0, background tasks): a native "running as a background task" result is uncertain, never a firm empty capture. Rebind it to the task's later `manage_task` status DONE output or the `Task id <id> finished with result` SYSTEM_MESSAGE, matched by task ID, and judge that output. Abstain when neither exists.
4. Finding 2: a successful wrapper or script (`bash x.sh`, `python3 script.py`) whose output the gate cannot classify is uncertain: abstain. Accept runner-agnostic zero-failure summaries (`# fail 0`, `Ran N tests ... OK`). Choose the compile or parse action per keyword; `dbt compile` covers a parse claim.
5. Finding 3: match ID keys case-insensitively without `_` (`runid`, `executionid`, `id`, `executionarn`); match a claimed name against `pipelineName` / `jobName`. A status read with a terminal status but no associable ID is unknown (abstain), never absent.
6. Finding 4: known non-terminal states (`inprogress`, `queued`, `running`, `notstarted`, `pending`, `in_progress`) contradict a completed claim and block. Missing or unknown status still abstains.
7. Findings 5-7: exclude modal or conditional verbs (`must`, `should`, `can be`), third-party subjects and prose being written into artifacts; visual claims need an image this turn's tools produced. Skip failed count queries when a later one succeeded. Read every row of a text table until a blank line or `rows affected` footer.
8. Re-run the sweep on your final HEAD. Report: total flags, each of the reviewer's adjudicated false positives (pass or still blocked), and every new flag not in the baseline with a one-line judgement. Target: every adjudicated false positive is gone and no new false flag appears. If one cannot be removed safely, record why.
9. Run `uv run pytest -q tests` and `uv run ruff check` on changed files. Respect the 300-line module limit in `tests/test_static_analysis.py`. Commit on `main` (one or more commits). Do not push (Kai asked to keep commits local). The post-commit hook syncs installed copies.

## Done
- Every finding 1-7 has at least one new test from the reviewer's evidence; full suite passes.
- Sweep re-run on the final HEAD, with the before/after table.
- Default stays shadow (`AGY_STOP_AUDIT_BLOCK_TAGS` default empty). `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` is empty.
- Report: write it in your final reply (you may be unable to write files outside the repo); the lead saves it as report6.md. Include a table "Finding | Test name | Fix commit | Status" (first cell is the finding number), the pytest summary, a "## Live test" section with the sweep before/after and realturn.py output for every adjudicated false positive, assumptions, and open paths.

## On failure
If a fix needs `hooks.json` edits, Compass model changes or a new dependency, stop and say why. If a finding cannot be fixed safely, make that path abstain and record it.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. Do not run live agy sessions, do not call Jev or any remote classifier, do not write to /tmp/agy_stop_audit/. No messages to anyone.
