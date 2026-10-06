Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Your first change (`df1d316`) made the agy Stop hook block unsupported completion claims. A read-only review replayed real sessions and reproduced false blocks and one unsafe path, so the lead set the default to shadow (`a83f649`, `AGY_STOP_AUDIT_BLOCK_TAGS` default empty). Fix the defects so that blocking CLAIM can be turned on again later. Kai's rules still hold: block only unsupported claims; a normal Q&A turn is never steered; any doubt about evidence means abstain, not block.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `a83f649`. Untracked `demo-video/.env`: do not touch or commit.
- Review with reproductions: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review.md (findings 1-8, file:line, examples). Read it fully first.
- Original brief: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/prompt.md.

## Steps
For each finding, first add a failing test that uses the reviewer's exact example (anonymized: example.com, Client A, REDACTED), then fix, then confirm the test passes.
1. Finding 1: apply the same current-turn `has_execution_or_edit` admission guard to FAIL as to CLAIM, sliced once per event. Exclude earlier-turn artifact content from current failure support.
2. Finding 2: exclude fenced code blocks and escaped quotes from assertions; keep a real assertion whose command name is inline code (`` `dbt build` passed. `` is a claim). Add the `f48cc75e`-shaped fixture.
3. Finding 3: accept the native agy header `The command exited with code 0.` (and non-zero as failure) through one shared status parser; failure wins over success.
4. Finding 4: keep pending tool calls across planner records and pair results by identity or order; if pairing is ambiguous, abstain (no missing-receipt block).
5. Finding 5: a pipeline/job claim needs a readback of the same run (id match when the claim names one) with a terminal success status parsed from that record; a failed status in the record rejects it. Vietnamese completion phrases need a receipt for the same operation, not any exit-zero command.
6. Finding 6: a "counts match" claim needs labelled source/target values that are equal, or an explicit difference/mismatch query result of zero. A multiset of integers is not proof.
7. Finding 7: strip ANSI codes; read dbt `PASS=`/`ERROR=` across the bounded summary (line breaks allowed); route `dbt test passed` through the dbt receipt check, not the pytest command check.
8. Finding 8: an API or query run through `python3 -c`, `sqlcmd -i file.sql` or similar wrappers is execution, not echoed text. If the wrapper cannot be audited, abstain.
9. Run `uv run pytest -q tests` and `uv run ruff check` on changed files. Commit on `main` (one or more commits), push. The post-commit hook syncs installed copies.

## Done
- Every finding has at least one new test from the reviewer's example; full suite passes.
- Default stays shadow (`AGY_STOP_AUDIT_BLOCK_TAGS` default empty). Do not re-enable blocking.
- `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` is empty.
- Report: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report2.md with a table "Finding | Test name | Fix commit | Status", pasted pytest summary, and a section "Live test": run, in a scratch dir, `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM agy -p "Run: echo ok. Then reply exactly: The command exited with code 0 and the build passed." --model "Gemini 3.8 Flash (High)"` is not needed; instead replay the reviewer's offline examples through the real hook with Compass stubbed and paste the outputs (blocked vs not blocked) for findings 1, 2, 3, 5, 6, 7.

## On failure
If a fix needs `hooks.json` edits, Compass model changes or a new dependency, stop and write why in report2.md. If a finding cannot be fixed safely, make that path abstain and record it.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. Do not run live agy sessions that would steer real work. No messages to anyone.
