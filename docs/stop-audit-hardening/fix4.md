Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Round 4. Your commit `733abe7` closed the second review, but the third review found an over-correction: every native multi-call turn is now marked ambiguous as a whole, so test/pipeline CLAIM checks detect nothing (0/738 outputs bound in 15 real sessions), even for explicit ID-bound failures. It also reproduced 7 more defects (positional count parsing, inspection text cancelling wrapper abstention, FAIL support from success summaries and command text, FAIL support unrelated to the claimed operation, zero-exit compiler failure, status borrowed across records, missing capture treated as absence). Default stays shadow. Kai's rules still hold: block only unsupported claims; a normal Q&A turn is never steered; any doubt about evidence means abstain, not block.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `733abe7`. Untracked `demo-video/.env`: do not touch or commit.
- Review with reproductions: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review3.md (section "Findings, ranked", findings 1-8, file:line, evidence E1-E8, "Smallest fix" per finding). Read it fully first.
- Earlier reviews and your reports, for context only: sol-review.md, sol-review2.md, report2.md, report3.md in the same folder. Original brief: prompt.md in the same folder.

## Steps
1. For each finding in sol-review3.md, first add a failing test from the reviewer's exact reproduction (anonymized), then apply the reviewer's "Smallest fix", then confirm the test passes. Keep every earlier control passing.
2. Finding 1 is the priority. Track uncertainty per call or per operation, never as a whole-turn veto. Bind strictly serial native call/result windows (each result arrives before the next call starts). Abstain only for calls whose windows genuinely overlap without IDs. An explicitly ID-bound failed operation must still block even when unrelated ID-less calls exist. Do not restore global FIFO across overlapping planners.
3. Finding 8: an attempted operation with no captured result is unauditable for that operation: abstain, do not block.
4. Principles: one shared status parser; status fields come from one record only; counts are labelled by column name, not position; FAIL support uses only failure evidence for the claimed operation that is current, captured and not superseded by a later success; success summaries and command text are never failure evidence.
5. Add one regression test that replays a natural-shaped multi-call native turn (serial calls, mixed success and one explicit failure) and asserts outputs are bound (not all ambiguous).
6. Run `uv run pytest -q tests` and `uv run ruff check` on changed files. Commit on `main` (one or more commits), push. The post-commit hook syncs installed copies.

## Done
- Every finding in sol-review3.md has at least one new test from the reviewer's reproduction; full suite passes.
- Default stays shadow (`AGY_STOP_AUDIT_BLOCK_TAGS` default empty). Do not re-enable blocking.
- `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` is empty.
- Report: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report4.md with a table "Finding | Test name | Fix commit | Status" (one row per finding, first cell is the finding number), pasted pytest summary, and a section titled exactly "## Live test" pasting offline replays of the real hook (Compass stubbed, `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM,FAIL` set only in the test process) for each finding: the reviewer's failing case and its control, blocked or not.

## On failure
If a fix needs `hooks.json` edits, Compass model changes or a new dependency, stop and write why in report4.md. If a finding cannot be fixed safely, make that path abstain and record it.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. Do not run live agy sessions that would steer real work. No messages to anyone.
