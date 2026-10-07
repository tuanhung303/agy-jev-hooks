Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Round 5. Round 4 (`60a3df8`) restored native binding (702/738 call slots bound, 0/15 false blocks on real sessions), but the fourth review reproduced 8 remaining defects, several introduced by round 4: an ID-less call steals an explicit result (false block), missing capture behind a pipe still blocks, uncertainty is still global across claim sentences, one overlap disables later binding for the whole turn, status and count records still borrow or overwrite fields, and FAIL still cites repaired dbt failures and unrelated artifact text. Earlier rounds each fixed one set and opened another; avoid that by fixing the shared mechanisms, not the single examples. Default stays shadow. Kai's rules still hold: block only unsupported claims; a normal Q&A turn is never steered; any doubt about evidence means abstain, not block.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `10860b2` (code at `60a3df8`; `10860b2` only adds docs and is not pushed). Untracked `demo-video/.env`: do not touch or commit.
- Review with reproductions: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review4.md (section "Remaining findings, ranked", findings 1-8, file:line, evidence E-R1 to E-R8, "Smallest fix" per finding; reviewer scratch replays in /tmp/review60a3df8/ if still present). Read it fully first.
- Earlier reviews and your reports, for context only: sol-review.md, sol-review2.md, sol-review3.md, report2.md to report4.md in the same folder (also committed under docs/stop-audit-hardening/). Original brief: prompt.md in the same folder.

## Steps
1. For each finding in sol-review4.md, first add a failing test from the reviewer's exact reproduction (anonymized), then fix, then confirm the test passes. Keep every earlier control passing, including all tests added in rounds 1-4.
2. Binding (findings 1, 4): one result is consumed at most once. Results that name an explicit ID are never positional candidates. Track every outstanding call (ID or not); a window is serial only when no other call is outstanding. After an overlapping window fully settles, resume serial binding. Do not restore global FIFO across overlaps.
3. Uncertainty (findings 2, 3): compute coverage and uncertainty per assertion and per operation. An uncertain operation never erases a captured failure of a different operation. Missing capture for the executed stage of a pipe (`dbt build | tail -5`) or a deployment observation abstains for that claim only.
4. Records (findings 5, 6): one shared record parser returns, per record, the ID/status or source/target counts from that record only, or "unknown association". No whole-body field fallback. Select the record for the claimed ID; conflicting records for the same comparison mean mismatch or abstain, never a merge. Unknown association abstains.
5. FAIL support (findings 7, 8): use one success/failure interpreter for initial and superseding receipts (`PASS=76 WARN=9 ERROR=0` is success on one line or split). Normalize commands without the display prefix (`$ `) and launchers (`uv run`); a later success of the same dbt action and target supersedes the earlier failure. Artifact content supports FAIL only when it matches the claimed operation or target and records an observed outcome.
6. After the fixes, replay the 15 natural sessions listed in sol-review4.md "Precision" through the real hook offline; report bound/ambiguous counts and any session that now blocks (must stay 0 false blocks).
7. Run `uv run pytest -q tests` and `uv run ruff check` on changed files. Commit on `main` (one or more commits). Do not push: Kai asked to keep commits local. The post-commit hook syncs installed copies.

## Done
- Every finding in sol-review4.md has at least one new test from the reviewer's reproduction; full suite passes.
- Default stays shadow (`AGY_STOP_AUDIT_BLOCK_TAGS` default empty). Do not re-enable blocking.
- `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` is empty.
- Report: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report5.md with a table "Finding | Test name | Fix commit | Status" (one row per finding, first cell is the finding number), pasted pytest summary, and a section titled exactly "## Live test" pasting offline replays of the real hook (Compass stubbed, `AGY_STOP_AUDIT_BLOCK_TAGS=CLAIM,FAIL` set only in the test process) for each finding: the reviewer's failing case and its control, blocked or not; plus the natural replay table from step 6.

## On failure
If a fix needs `hooks.json` edits, Compass model changes or a new dependency, stop and write why in report5.md. If a finding cannot be fixed safely, make that path abstain and record it.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. Do not run live agy sessions that would steer real work. No messages to anyone.
