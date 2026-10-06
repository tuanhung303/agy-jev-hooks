Persona: Senior reviewer for agent hooks, read-only. Review a change that makes the Antigravity (agy) Stop hook block unsupported completion claims.

## Why
Kai's problem: Gemini in agy reports steps as passed that it never ran. The hook now blocks such turns (CLAIM and FAIL gates) by default. A false block on a normal turn is costly: it re-invokes the agent and wastes Kai's time. Decide whether default blocking is safe to keep, and find defects.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, commit `df1d316` on `main` (review `b5ddf58..df1d316`). Installed copy: `~/.config/agy/agy-stop-audit.py` (registered in `~/.gemini/config/hooks.json`, entry "agy-stop-audit").
- Brief the implementer worked from: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/prompt.md. Implementer report: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report.md.
- Audit log: /tmp/agy_stop_audit/audit.log (tags PASS, CLAIM, FAIL, WOULD_<tag>, WOULD_CLAIM_BG). Transcripts resolve through the hook's `resolve_transcript_path`.
- Lead-verified so far: full test suite passes (717 passed, 2 skipped); installed copy equals the repo; live session `2fe209cf` logged CLAIM then PASS; Q&A session `cd11f35e` logged PASS.

## Questions
1. Precision on natural traffic. The implementer's sample is 6 self-made probes plus 4 real sessions (2 judged false positive under old rules). Take recent real agy sessions that ran tools (not the probes `0005219f`, `2fe209cf`, `4bc0a214`, `81995020`, `400d4320`, `c164a16f`, `a43fc03d`), up to 15, and replay the current claim contract and gate logic on each final turn offline (no Compass calls needed for CLAIM; say how you judged FAIL). Report true positives, false positives and the rate. Separately: how often does the FAIL (Jev Compass) gate fire, and is it reliable enough to block?
2. Q&A safety: can any turn without an executing or editing tool call get a CLAIM or FAIL steer? Check the code path, not only the tests.
3. Receipt matching: can a real dbt/pipeline/count receipt be missed (false block), for example dbt output split over lines, ANSI colour codes, `Done. PASS=76 WARN=9 ERROR=0`, Fabric job status JSON, sqlcmd tables?
4. Fail-open, the steer cap, the fullyIdle path (`WOULD_CLAIM_BG` logs only, emits `{}`), and the shared module's effect on hooks/claude-stop-audit.py (must stay shadow).
5. Anything in the commit that leaks real client names or secrets into fixtures.

## Output
Write /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review.md. First line: `keep blocking`, `keep blocking with fixes`, or `switch to shadow`. Then a "Precision" table (session, verdict, one-line reason) and the rate. Then findings ranked, max 8, each with file:line, reproduced evidence and the smallest fix.

## Boundaries
Read-only: no edits, commits, pushes, hook config changes or agy runs that write to the audit log. Running `uv run pytest` and offline Python against copies is fine. No messages to anyone.
