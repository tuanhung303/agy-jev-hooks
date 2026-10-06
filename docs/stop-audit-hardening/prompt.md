Persona: Hook engineer for the agy-jev-hooks repo. Scope: the Antigravity (agy) Stop audit and the shared claim contract. No other repos.

TASK: Make the agy Stop hook enforce the reporting contract for every agy session, interactive or headless (`agy -p`). Kai's goal: Gemini "báo pass cho bước nó không chạy" must be stopped by the hook, and normal Q&A turns must pass untouched. End state: CLAIM and FAIL gates steer for real (block), CASUAL and SKILL stay shadow, pipeline/data claims need a receipt, and a cheap claim check also runs while background tasks are active (log only).

CONTEXT: Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, clean except untracked `demo-video/.env` (do not touch, do not commit). Commits auto-sync to `~/.config/agy`, `~/.gemini/config/hooks` and `~/.qoder/hooks` via the git post-commit hook running `scripts/sync.sh` (= `scripts/install.sh --copy`).

VERIFIED: The hook is registered on agy Stop as `python3 /Users/__blitzzz/.config/agy/agy-stop-audit.py`. Evidence: `~/.gemini/config/hooks.json`, entry "agy-stop-audit".
VERIFIED: It runs in shadow mode: one global switch. Evidence: hooks/agy-stop-audit.py:12-13 and :285 (`if os.environ.get("AGY_STOP_AUDIT_MODE") != "block": log(f"WOULD_{tag} ...")`). `AGY_STOP_AUDIT_MODE` is not set anywhere (hooks.json, ~/.config/agy/sage.env, launchctl).
VERIFIED: Gate chain in main(): claim contract (tag CLAIM) -> Jev Compass (FAIL) -> stop skill pack (SKILL) -> casual restyle (CASUAL). Evidence: hooks/agy-stop-audit.py main(), relative lines 67-96.
VERIFIED: Log /tmp/agy_stop_audit/audit.log (661 lines on 2026-10-05): 227 PASS, 36 WOULD_CASUAL, 27 WOULD_CLAIM, 17 WOULD_FAIL, 12 WOULD_SKILL, 198 "fullyIdle is False; abstaining". Evidence: `grep -oE "WOULD_[A-Za-z_]+|PASS" audit.log | sort | uniq -c`.
VERIFIED: fullyIdle abstain is at hooks/agy-stop-audit.py:215-216 and returns before any gate.
VERIFIED: Claim contract lives in sage/claims.py. Claim regexes cover tests (TEST_CLAIM_RE :17), deploys (DEPLOY_CLAIM_RE :20) and screenshots only; `uncovered_claims` :208, `claim_contract_hint_for` :265. sage/claims.py is shared with hooks/claude-stop-audit.py.
VERIFIED: Steers are capped at MAX_STEERS_PER_SESSION = 2 per session (hooks/agy-stop-audit.py). Kill switches: AGY_SAGE_DISABLED=1, AGY_STOP_AUDIT=0, AGY_JEV_GATE=0.
NEEDS VERIFICATION: A Stop steer (`{"decision": "continue", "reason": ...}`) re-invokes the agent in headless `agy -p` mode, not only in the app. Check: the live test in ACCEPTANCE 4.

DECISIONS (Kai, 2026-10-05):
1. Block only CLAIM and FAIL. CASUAL and SKILL stay shadow (log WOULD_<tag>).
2. Add pipeline/data claims to the claim contract.
3. Run the cheap claim check (no LLM, no Compass) when fullyIdle is False, log only (`WOULD_CLAIM_BG ...`), never steer, then abstain as today.
4. Normal Q&A must never be steered.

REQUIREMENTS:
- Per-tag mode. Replace the single switch with `AGY_STOP_AUDIT_BLOCK_TAGS` (comma list, default `CLAIM,FAIL`). `AGY_STOP_AUDIT_MODE=shadow` forces all tags to shadow; `AGY_STOP_AUDIT_MODE=block` blocks all tags (keep backward compatible). Default with nothing set = block CLAIM and FAIL.
- Pipeline/data claims in sage/claims.py: genuine assertions (reuse `_is_assertion`; quoted, negated and conditional text are not claims) such as "dbt build passed", "PASS=69 ... ERROR=0", "pipeline/job/run succeeded|completed", "compiled clean|parse clean", "row counts match|reconciled". Each needs a receipt in the same turn: a command whose output shows the matching result (dbt `Done. PASS=` with `ERROR=0`; a job/pipeline status read-back containing a run ID and Succeeded/Completed; a count query whose output contains the claimed numbers). Missing receipt -> uncovered claim, same hint format as test claims. Include Vietnamese forms the file already handles in the same style ("đã chạy xong", "build pass").
- Q&A safety: a turn with no tool call that executes or edits anything must produce no CLAIM steer, even if the reply contains "pass" or "done". Add this as an explicit guard if the current code does not already guarantee it; prove it with a test.
- Shared module: the new claim kinds also reach hooks/claude-stop-audit.py through sage/claims.py. Keep Claude behaviour shadow by default as today; run its tests.
- Keep fail-open everywhere: any exception -> `{}` and exit 0.
- Code style: match the surrounding code; short comments; no new dependencies.

OUTPUT: Commits on `main` in agy-jev-hooks (one per decision is fine), pushed if the repo has an upstream. Report to /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report.md: commits, pasted test output, live test evidence, the precision sample table, assumptions.

SKILLS: tdd: write the failing test for each new behaviour first.

GOTCHAS:
- Do not change the steer cap, the fullyIdle abstain for steering, or the Compass budget.
- Do not edit installed copies in ~/.config/agy or ~/.gemini/config/hooks by hand; commit in the repo and let sync.sh copy.
- A naive regex on "pass" or "done" fires on Q&A and quotes. Use the existing assertion filter.
- Test fixtures must be anonymized (repo AGENTS.md: Client A, example.com, REDACTED).

ACCEPTANCE:
1. `uv run pytest -q tests` passes (paste the summary line). New tests cover: per-tag mode matrix (default, MODE=shadow, MODE=block, custom BLOCK_TAGS); each pipeline/data claim kind with and without receipt; negated/quoted claims not counted; Q&A turn with no tool calls never steered; fullyIdle=False logs WOULD_CLAIM_BG and emits `{}`.
2. Precision sample before relying on block mode: take the 10 most recent WOULD_CLAIM and WOULD_FAIL entries in /tmp/agy_stop_audit/audit.log, open each session transcript (path via the hook's own `resolve_transcript_path`), and mark each true or false positive with one line of reason. If more than 3 of 10 are false positives, set the default BLOCK_TAGS to empty (shadow), finish the rest, and report.
3. `scripts/sync.sh` ran; `diff` between repo hooks/agy-stop-audit.py and ~/.config/agy/agy-stop-audit.py is empty.
4. Live test, headless: in a scratch dir, run `agy -p "Reply exactly: All tests pass and the dbt build passed." --model "Gemini 3.8 Flash (High)"` after one trivial command, and a pure Q&A prompt (`agy -p "What does SCD2 mean? One sentence."`). The audit log shows a CLAIM line (not WOULD_CLAIM) for the first and PASS for the second. Paste the log lines.

FIRST ACTION: Read hooks/agy-stop-audit.py main() and sage/claims.py uncovered_claims(), then run `uv run pytest -q tests/test_agy_stop_audit_contract.py tests/test_claims.py` to get the baseline.

BOUNDARIES: Do not touch other repos, ~/.gemini/config/AGENTS.md, hooks.json, or the Jev Compass model config. No messages to anyone. Stop and report if a change would need hooks.json edits.

ASSUMPTIONS: The false-positive threshold (3 of 10) is our choice. Claude's stop audit stays shadow. Correct if wrong.
