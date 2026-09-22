---
id: teamplay
origin: local
source: /Users/__blitzzz/Documents/GitHub/agentic/skills/teamplay/SKILL.md
description: Coordinate independent workstreams through one lead session: ownership, dependencies, verified integration.
triggers: [teamplay, orchestration, delegate, workstream, assignments]
inject: prompt
---

# teamplay

Lead coordinates independent work, one session. Delegate for speed, coverage, independent verification. Small or coupled work stays local. Lead: planning, coordination, verified integration.

## Coordinate

1. Goal, constraints, acceptance, dependencies first. Continue: reuse session/plan. New session when needed. Delegate independent work unprompted.
2. Participant: absolute session path, assignment ID, context, scope, acceptance. Sequence overlaps, preserve unrelated work, no duplicated assignments.
3. Lead alone owns `session.yaml` and `review-state.json`; participants return results plus updates. User message verbatim, split from derived requirements, decisions, facts, assumptions. Plans, evidence, credential locations: reference, never copy.
4. Context first. Material change: lead updates plan plus revision, notifies, pauses conflict until acknowledged. Ownership changes, blocked dependencies: through lead.
5. Returns: revision used, results, evidence, unresolved items. Evidence names exact artifacts and checks run. Lead verifies acceptance vs current artifacts. Independent review: requirements plus immutable artifact revisions only, no reasoning or verdicts. Changed artifact voids its verdict. Independence unavailable: disclose. Done = outcome verified, all assignments accounted. Blocked or cancelled does not satisfy.

## Size assignments

Time to verified completion: briefing, waiting, integration, review.

1. Contracts early: ownership, units, date/grain, interfaces, scope. Boundary case on real artifacts before dependents. Open decisions explicit; independent work only.
2. One owner per coherent outcome (layers, tests, docs). Never per file or fix. File count does not set size. Each assignment records resource scopes in `session.yaml`; every write, shell and API writes included, stays inside its declared scope. Fence the prior owner before reassigning a scope. Overlapping scopes across sessions: serialize, or arbitrate through one authority.
3. Split: isolatable ownership or independent criticism wanted. Coupled edits: one owner. Start lead plus one builder.
4. Boundary = autonomy: outcome, constraints, ownership, decisive examples, acceptance evidence. Routine impl = worker call. Cheap fixes stay local.
5. Repairs batched to existing owner. Reuse context, evidence. Recheck changed behavior plus dependencies. Broad suites only on real risk. Keep integration verification, independent criticism.
6. Coordination dominating: consolidate or clarify contract. Repeated unrelated failures: split. Track outcomes, scope, delays.

## Review and repair loop

Worker result = attempt until lead verifies vs current artifacts. Continue: actionable confirmed defect, unmet requirement, or evidence gap. All confirmed defects fixed, low severity included. Done = integrated verified plus independent falsification attempt. New defect reopens acceptance. Never stop on worker exit, round count, one suite pass. Stall: change investigation or assignment. Hard limit: incomplete handoff plus remaining work. `review-state.json` = Version-1 JSON record. Lead closes only on full accounting.

## Start session

Caller: ID, new session dir, workspace. `TEAMPLAY_DIR` = skill dir. No `--message-file` = stdin. `--conversation-ref` optional. Script prints absolute `session.yaml` path, keeps message verbatim, refuses existing destination. Fill goal, assignments before delegating.

```bash
python3 "$TEAMPLAY_DIR/scripts/start_session.py" \
  --session-id "$SESSION_ID" --path "$SESSION_DIR" \
  --workspace "$PROJECT_DIR" --message-file "$REQUEST_FILE"
```

## Session shape

Absolute paths, stable coordination ID. Conversation refs: system plus exact conversation per participant. History inaccessible: context inline. `plan_path`/`review_path` nullable; `review_path` points to `review-state.json`.

```yaml
session_id: "<stable ID>"      # stable coordination ID
revision: 1
workspace: "<absolute path>"
context:
  user_messages: [{ref: initial, content: "<verbatim user message>"}]
  verified_facts: []           # entries carry evidence refs
assignments:                   # state: pending|running|reported|verified|blocked|cancelled
  - {id: A1, outcome: "<bounded deliverable>", scope: [], depends_on: [],
     acceptance: [], status: pending, context_revision: 1, evidence_refs: []}
```

Also null/empty: `goal`, `plan_path`, `review_path`, `conversations`, `user_requirements`, `constraints`, `decisions`, `assumptions`, `owner`, `conversation_ref`, `result_ref`. Reported: lead verification. Blocked: blocker plus next action. Confirm cancellation before releasing ownership.
