Persona: Senior reviewer for agent hooks, read-only. Review round 4 of a change that lets the Antigravity (agy) Stop hook block unsupported completion claims.

## Why
Kai's problem: Gemini in agy reports steps as passed that it never ran. The hook can block such turns (CLAIM and FAIL gates) but the deployed default is shadow (empty `AGY_STOP_AUDIT_BLOCK_TAGS`). A false block on a normal turn is costly: it re-invokes the agent and wastes Kai's time. Decide whether CLAIM blocking is now safe to enable, and find remaining defects. FAIL is judged separately.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, commit `60a3df8` on `main` (fixes for your third review); review `733abe7..60a3df8`. Installed copy: `~/.config/agy/agy-stop-audit.py` and modules (registered in `~/.gemini/config/hooks.json`, entry "agy-stop-audit").
- Your previous review: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review3.md (findings 1-8, E1-E8, the 15-session natural sample). Fix brief: fix4.md. Implementer report: report4.md (same folder).
- Audit log: /tmp/agy_stop_audit/audit.log. Transcripts resolve through the hook's `resolve_transcript_path`.
- Lead-verified so far: full suite 757 passed, 2 skipped (acceptance script re-ran it); installed hook equals the repo; default block-tag list is empty; hooks.json registration still points to the installed hook. The post-commit installer rewrote hooks.json (mtime changed); check whether its content changed in a way that matters.

## Questions
0. For each finding in sol-review3.md: closed, partly closed or open, with your own reproduction against `60a3df8`. Re-run E1-E8 and their controls.
1. Natural traffic: replay the same 15 real sessions from your third review, plus any newer real tool-using agy sessions (exclude probes), on the final turn offline. Report how many outputs are now bound vs ambiguous, true and false CLAIM positives, and the rate. Did any session that passed before now block?
2. Regression hunt: did the per-call uncertainty change reopen any finding from sol-review.md or sol-review2.md? Re-run those reproductions.
3. Q&A safety, fail-open, steer cap, fullyIdle path, and Claude hook staying shadow: confirm still true.
4. Fixtures: no real client names or secrets.

## Output
Write /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review4.md. First line exactly one of: `enable CLAIM blocking`, `keep shadow`, `keep shadow with fixes`. Then a table for sol-review3 findings 1-8. Then a "Precision" table (session, bound/ambiguous outputs, verdict, one-line reason) and the rate. Then new findings ranked, max 8, each with file:line, reproduced evidence and the smallest fix. Keep the report under 200 lines.

## Boundaries
Read-only: no edits, commits, pushes, hook config changes or agy runs that write to the audit log. Running `uv run pytest` and offline Python against copies is fine. No messages to anyone.
