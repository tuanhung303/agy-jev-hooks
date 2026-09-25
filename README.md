# agy-jev-hooks

Stop verifier hooks for Antigravity (`agy`) and Qoder agents. When a turn ends, the hook checks whether the requested work is done and injects steering when it is not.

Requires Python 3.10+, no runtime Python dependencies, and an authenticated `agy` CLI.

## How it works

1. Recover the active request, earlier constraints, and relevant tool evidence. Read-only tasks are audited too, including premature deferrals when no edits were made.
2. Fork the conversation database into an isolated home, leaving the parent conversation untouched.
3. Ask the Jev Compass classifier to judge the turn. A hard-escalate label above its floor fails the stop with an actionable steer. Quality notes are logged and never block. Anything else is a clean or unavailable stop.
4. Validate the verdict structure and check cited absolute local media paths. A missing or empty image triggers one reconsideration with the original context.
5. Inject the task-specific action when work remains. Complete, blocked, unavailable, and retry-exhausted states stay distinct in persisted state and the statusline.

The model writes every corrective action. There is no scripted repair fallback, planning interview, required verification directory, or file-age threshold. Judgment is one Compass classification per stop, bounded by `LITE_MODE_TIMEOUT` (default 20 seconds). `AGY_COMPASS_ENABLED=0` disables it. Every Jev failure fails open to an unavailable or clean stop with no invented steering. Three rejection strikes and bounded duplicate-action replays prevent loops.

## Install

```sh
./scripts/install.sh --copy
```

Copy mode bypasses macOS TCC sandbox boundaries. The `post-commit` and `post-merge` git hooks run `scripts/sync.sh`, so commits propagate to `~/.config/agy`, `~/.gemini/config/hooks`, and `~/.qoder/hooks` automatically.

## Modules

| Path | Responsibility |
|---|---|
| [hooks/agy-stop-audit.py](hooks/agy-stop-audit.py) | Antigravity Stop audit: native transcript replay, token drain protection, gate chain |
| [hooks/zcode-stop-audit.py](hooks/zcode-stop-audit.py) | ZCode Stop audit: rollout replay into sage steps, gate chain |
| [hooks/qoder-stop-audit.py](hooks/qoder-stop-audit.py) | Qoder Stop audit |
| [hooks/claude-stop-audit.py](hooks/claude-stop-audit.py) | Claude Code Stop audit: claim contract on edit turns plus Compass, shadow log by default (`CLAUDE_STOP_AUDIT_MODE=block` to steer) |
| [hooks/hermes-stop-review.py](hooks/hermes-stop-review.py) | Hermes pre_llm_call cache + pre_verify gate |
| [sage/jev/evidence/](sage/jev/evidence/assemble.py) | Five-block evidence assembly, redaction, blast-radius detection |
| [sage/jev/verdict/compass.py](sage/jev/verdict/compass.py) | Jev Compass verdict, axis split, hard-label steer, fail-open handling |
| [jev.yaml](sage/jev/jev.yaml) | Single YAML config: cases, routing (categories, axes, pairs), skill route table |
| [scripts/qoder-fork.py](scripts/qoder-fork.py) | Qoder session fork helper |
| [hooks/skill-recommender.py](hooks/skill-recommender.py) | Pre-invocation skill suggestion router |
| [hooks/command-timer.py](hooks/command-timer.py) | Command duration feedback |

## Verify

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify/all.py --topic stop_verifier
.venv/bin/python scripts/verify/jev/empirical_pairs.py
```

Empirical pair tests need a Jev API key and consume model usage.

## Limits

Model judgment stays probabilistic. Bounded summaries can omit details, and ambiguous tool-result attribution stays unknown. Local media checks establish existence, not image content. The fork isolates conversation state, while the inspection-only prompt is not a filesystem sandbox. Active read-only requests incur model latency.
