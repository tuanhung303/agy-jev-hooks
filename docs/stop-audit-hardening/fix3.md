Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Round 3. Your fix commit `bcb5503` closed most of the first review. The second review replayed 15 real sessions with 0 false CLAIM blocks, but reproduced 8 new defects (SQL table parsing, native-wrapped status JSON, Vietnamese completion routing, FAIL support from repaired or stale failures, cross-planner positional binding, blanket wrapper abstention, lost structured status, zero-error compiler output). Default stays shadow. Fix these so CLAIM blocking can be enabled. Kai's rules still hold: block only unsupported claims; a normal Q&A turn is never steered; any doubt about evidence means abstain, not block.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `bcb5503`. Untracked `demo-video/.env`: do not touch or commit.
- Review with reproductions: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review2.md (section "Findings, ranked", findings 1-8, file:line, exact transcripts). Read it fully first. Earlier review and your report: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review.md, /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report2.md.
- Original brief: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/prompt.md.

## Steps
For each finding in sol-review2.md, first add a failing test from the reviewer's exact reproduction (anonymized), then apply the reviewer's "Smallest fix", then confirm the test passes. Keep the previous controls passing. Principles: one shared status parser that keeps structured status (exit_code, isError) and the native header; unknown or unauditable evidence abstains for the affected operation only, and explicit matching failure evidence still blocks; never infer equality from footers or arbitrary integers; FAIL support uses only current, captured, not-superseded evidence for the claimed operation.
Then run `uv run pytest -q tests` and `uv run ruff check` on changed files. Commit on `main` (one or more commits), push. The post-commit hook syncs installed copies.

## Done
- Every finding in sol-review2.md has at least one new test from the reviewer's reproduction; full suite passes.
- Default stays shadow (`AGY_STOP_AUDIT_BLOCK_TAGS` default empty). Do not re-enable blocking.
- `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` is empty.
- Report: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report3.md with a table "Finding | Test name | Fix commit | Status", pasted pytest summary, and a section "Live test" pasting offline replays of the real hook (Compass stubbed, `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM` set only in the test process) for each finding: the reviewer's failing case and its control, blocked or not.

## On failure
If a fix needs `hooks.json` edits, Compass model changes or a new dependency, stop and write why in report3.md. If a finding cannot be fixed safely, make that path abstain and record it.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. Do not run live agy sessions that would steer real work. No messages to anyone.
