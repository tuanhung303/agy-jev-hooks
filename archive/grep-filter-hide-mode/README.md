# Archived: Grep filter hide mode

Retired on 2026-09-28. `hooks/claude-grep-filter.py` no longer hides search
output. It ranks the matches with Jev and, in `hint` mode, tells Claude which
files and lines to read first. Nothing the search printed is ever removed.

What was cut, and why:

| Cut | Why |
|---|---|
| `filter` mode: rewrite Grep / Bash output without low-scored files (`filtered_response`, `filtered_bash_response`, `_drop_orphan_separators`) | Live, it hid the file the agent needed. TypeSafe's rerank cookbook puts the answer at top 1 18% of the time and in the top 10 62%: good enough to order a list, not to drop from it. |
| Repeat bypass (`_seen_marker`, `_claim`, `_release`) | It only existed so a hidden result could be seen by repeating the call. |
| `too_broad` and `none_relevant` notes to Claude | Filter-mode messages. Both outcomes are still logged. |
| `CLAUDE_GREP_FILTER_DELIVERY=context` | A synchronous hook made every search wait 5-25 s for Jev. The hint now always goes out as exit-2 stderr, which asyncRewake delivers when Jev answers. |
| "Current step" in the query (assistant text before the call) | Claude Code writes that text to the transcript after PostToolUse hooks run, so the hook read an older, stale step. The Bash description and the recent prompts carry the intent. |
| `CLAUDE_GREP_FILTER_BASH_MIN_BYTES` (4 KB gate) | Replaced by an exact gate before Jev: skip when every matched file is already on screen and fewer than `HINT_MIN_FILES` matched, since no hint can come of that call. 2 of 5 replayed searches had paid for a Jev call that ended in "hint not needed". |
| Engine `fragment_filter` invocation option | Superseded by `fragment_map`, where returning None drops a fragment. |

Archived here, not collected by pytest (`testpaths = ["tests"]`):

- `claude-grep-filter.py`: the hook as of commit 8e8c5c6, with all modes.
- `test_claude_grep_filter.py`: its tests as of the same commit.

To restore hide mode, copy the archived hook over `hooks/claude-grep-filter.py`
(or `git show 8e8c5c6:hooks/claude-grep-filter.py`), bring back the
`fragment_filter` branch in `mcp/jev/grep/engine.py` if needed, and run it with
`CLAUDE_GREP_FILTER_MODE=filter` as a synchronous hook (no `async`), since
`updatedToolOutput` only works before Claude sees the result.
