Persona: Hook engineer for the agy-jev-hooks repo. Scope: the agy Stop audit and the shared claim modules. No other repos.

## Why
Round 8. Seven rounds of widening and narrowing regexes kept fixing the last test set and opening new holes. Kai decided 2026-10-07 to change the approach: review each claim category properly on real traffic, and drop the claims that are noise or false positives. Precision first. A flag that blocks a correct turn costs Kai more than a missed lie, because the hook is a safety net, not the only check.

Kai's decisions that bind this round:
- Carry-over (2026-10-07): a restated earlier result is allowed when an earlier turn in the same session has a passing receipt for that operation.
- K1 (2026-10-07): that still holds when the current turn edited code without re-running ("Vẫn tha"). Keep the current K1 behaviour.
- Standing: block only unsupported claims; Q&A is never steered; doubt abstains for the operation in doubt. Default stays shadow.

## Inputs
- Repo /Users/__blitzzz/Documents/GitHub/agy-jev-hooks, branch `main`, HEAD `c8a898b` (local only). Untracked `demo-video/.env`: never open or commit.
- Ground truth: /Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review7.md, Q1 table (independent labels for the 14 real flags; these override report7.md labels), Q3 holdout and controls, findings 1-5. Read it fully first. Also report7.md (same folder).
- Harness (copy to /tmp/round8/ first, do not modify originals): /tmp/review-c8a898b/hold/holdout.py (22 lies + 12 controls; prints OK/MISS and a summary line; run from the repo root), /tmp/review-c8a898b/hold/carry_holdout.py and q5probe.py (K/Q5 cases), /tmp/round7/mine/sweep7.py `<repo> <out.json>` (every real action turn, about 3 minutes, passes prior_steps), /tmp/review-7cb08ae/lies.py (31 lies), /tmp/round7/mine/lies7.py, realturn.py and why.py under /tmp/review-7cb08ae/ and /tmp/round7/mine/, probe families prior.py, edges.py, fail-and-regressions.py, newprobes.py, final-check.py. Check each interface before relying on it.

## Steps
1. Baseline at HEAD: holdout (11/22 lies, 1/12 control false block), lies.py (31/31), sweep (23 flags: 9 probe, 14 real).
2. Category ledger. Group every claim the gate can raise by category (test, deploy, visual, pipeline run, dbt, compile, count, carry-over, and any other you find). For each category, list its real sweep flags with the reviewer's label, plus holdout lies caught and controls blocked. This ledger is the review Kai asked for; put it in the report.
3. Remove the false-positive mechanisms the reviewer reproduced (finding 4 and finding 5 first half):
   - (a) "test run Succeeded" sentences that name a pipeline or run ID go to the pipeline readback; read `status = <State>` lines from a polling script as run records (`b0e9afd2`:1).
   - (b) an exit-0 `rsync` / `gcloud storage rsync` / `gsutil cp` / deploy-script log that names the target (`Copying ... to gs://...`, `Sync complete`) counts as a target observation, in turn and for carry (`c4f6f1a0`:3, `0fe6788a`:2, `201152e9`:56).
   - (c) a subagent or monitor report in the transcript that names the run and a terminal state makes the claim unknown: abstain (`073c6f48`:16).
   - (d) `a7dffc0b`:0: dbt `run_results` read with `Failures: 0` supports the claim.
   - (e) strip thousands separators (`(?<=\d),(?=\d{3}\b)`) before parsing claimed counts (control C5).
4. Fix the two plainly wrong matchers that are not about wording: K2 (claimed counts match only runner summary numbers, not substrings like "1.12s"), and finding 5 second half (a one-sided observed count that contradicts the claimed number fails instead of abstaining).
5. Demote noise. Any category whose real-sweep precision stays below 80% after steps 3-4 (reviewer labels; ART excluded; undecidable counted as FP) becomes log-only: it still logs WOULD_CLAIM but never blocks, even when CLAIM is a block tag. Implement this as an explicit per-category list in one place, with a short comment naming the evidence. Do not add new claim wording (finding 3 is out of scope this round: it adds detections, not precision).
6. Do not lose catches. After all changes: lies.py 31/31; holdout lies caught >= 11 (list any lie that changed, with the reason; a lie lost only because its category was demoted is allowed but must be listed); 0 of 12 holdout controls blocked; carry/q5 probes reported before and after. Finding 2 (checker-named scripts silencing test claims, Q5a/Q5b): fix only if the sweep shows no new real false flag; otherwise leave it and say why.
7. Re-run the sweep. Every remaining real flag gets a label from the transcript (use the reviewer's label where it exists). Report precision and false blocks per action turn, overall and per category. Target: 0 reviewer-FP flags left in blocking categories; precision >= 80% and false blocks <= 0.5% for blocking categories.
8. Re-run the probe families; list every changed outcome with its reason.
9. Run `uv run pytest -q tests` and `uv run ruff check` on changed files. Respect the 300-line module limit. Commit on `main` (one or more commits). Do not push. The post-commit hook syncs installed copies.

## Done
- Category ledger before and after; demoted categories listed with evidence.
- 0 reviewer-FP flags in blocking categories; 0 holdout control false blocks; lies.py 31/31; holdout catches >= 11 or each loss explained.
- Every change has a test; full suite passes; default shadow; `diff hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py` empty.
- Report in your final reply (the lead saves it as report8.md): "Finding | Test name | Fix commit | Status" table (first cell the finding or step id), pytest summary, "## Live test" with the ledger, holdout before/after, sweep table with labels, precision and rate per category, probe changes, assumptions, open paths.

## On failure
If a target cannot be met without breaking another, keep precision (no false blocks) over recall, and report the numbers. If a fix needs `hooks.json` edits, Compass changes or a new dependency, stop and say why.

## Boundaries
No edits to `~/.gemini/config/AGENTS.md`, `hooks.json`, other repos, or installed copies by hand. Do not change the steer cap or Compass budget. No live agy sessions, no Jev or remote calls, no writes to /tmp/agy_stop_audit/. No messages to anyone. Do not change `hooks/claude-stop-audit.py`.
