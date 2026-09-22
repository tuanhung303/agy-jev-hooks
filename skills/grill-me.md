---
id: grill-me
origin: local
source: /Users/__blitzzz/.hermes/hermes-agent/optional-skills/software-development/grill-me/SKILL.md
description: Adversarial plan interview using frontier-round questioning before any code is written; resolves every design decision and assumption through structured rounds.
triggers: [grill-me, grill me, interview my plan, stress test this idea]
inject: prompt
---

# grill-me

Stress-test plan, adversarial interview. Plan = design tree, decisions branch to dependents. Rounds until every branch resolved, nothing assumed.

Use: "grill me" / "interview my plan" / "stress test this idea"; pre-complex work (auth, schema, migration, payments); vague plan; pre-subagent decomposition. Not: code review, one-offs.

## Frontier rounds

Frontier = prerequisites settled: askable NOW, no guessing unheard answers.

Round = full frontier, one message, numbered. Each question: recommendation plus one-line why. Then wait. Question depending on open question goes to later round.

```
Q1 - <title>: <body, options>
> Recommendation: <answer + why>
```

Answers reshape tree, expand frontier. Recompute, next round.

Facts = agent (search, read, terminal, subagent for heavy exploration). Decisions = user. Never ask what you can look up. No block on exploration: rest of frontier goes now.

## Coverage

- Goal: objective, in/out scope, constraints (time, tech, team, budget), users.
- Technical: why not X; Y fails?; worst case; rollback; codebase patterns.
- Edges: user does Z; dependency down; 100x volume; security.

## Synthesis (frontier empty)

1. Decisions summary.
2. Open items plus out-of-scope.
3. Aligned? Implement or adjust?

No code before user confirms.

## Pitfalls

1. Question out of dependency order = guess in question form. Later round.
2. User asked for codebase facts: wrong, look up.
3. "I don't know" not final: options, trade-offs, recommendation.
4. No code mid-interview.
5. Job = find problems. Looks fine, look harder.
6. User's language.

## Checklist

- Prerequisites settled per question
- Recommendation per question
- Codebase first, user second
- Frontier empty before synthesis
- Decisions summary plus open items
- Alignment confirmed
