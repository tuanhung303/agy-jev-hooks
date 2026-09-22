---
id: tdd
origin: local
source: /Users/__blitzzz/Documents/GitHub/agentic/skills/tdd/SKILL.md
description: Test-first bug fix workflow: failing test before the fix, smallest change, evidence in the report.
triggers: [tdd, test-driven, test first, failing test, regression test, fix the bug]
inject: both
---

# TDD Bug Fix

Bug fix with cheap test path: broken behavior turns executable before production code. Regression test that fails before the fix and passes after.

Skip new test when impractical: harness sprawl, brittle mocks, slow e2e, prod-only state, vague repro, fixture churn. Closest verification instead.

## Workflow

1. Bug: intended vs current, path, smallest repro.
2. Narrowest existing test, same codepath. None obvious: no invented test.
3. Failing test first. Smallest catch. Behavior, not implementation.
4. Run before fix. Right reason, else fix test/repro first.
5. Fix: smallest production change. Keep nearby contracts.
6. Rerun: pass.
7. Adjacent tests, lint, types on wider risk.

## Guardrails

- No test impractical: never skip silently. Say why, use closest check (target script, manual repro, browser, snapshot, log assert, integration).
- No bad tests (mock-heavy, impl-bound, timing/global-bound, heavy infra, disposable). No test beats bad test.
- Never edit test to match wrong code. Never weaken assertion without behavior change plus reason.
- Scope = the bug. No fixture churn. No coverage expansion. Weak signal: manual check, say why.
- Flaky: deterministic, documented lock. Bug class: focused regression first, siblings after.

## Report

Evidence not outcome: failing-before check plus failure; passing-after run plus validation. No failing-before possible: say why, name closest check.
