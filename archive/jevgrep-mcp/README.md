# Archived: jevgrep MCP server

Retired on 2026-09-28. The `semantic_search_code` MCP tool and the "search with
jevgrep first" prompt rules are removed from every harness (Claude, agy, Qoder,
ZCode). Agents search with Grep, and the Claude Code PostToolUse hook
`hooks/claude-grep-filter.py` uses the Jev engine to hide the low-relevance
files in noisy Grep results.

Still live, used by the Grep filter: the engine library in `mcp/jev/grep/`
(profiles, inventory, cache, credential quarantine, scoring) and the `jevgrep`
CLI for `init`, `doctor`, `inspect` and `search`.

Archived here, not collected by pytest:

- `mcp_server.py`: the stdio MCP server (was `mcp/jev/grep/mcp_server.py`).
- `test_jevgrep_mcp.py`: its tests (was `tests/test_jevgrep_mcp.py`).
- `test_review_regressions_mcp.py`: regression tests 5 and 6, cut from
  `tests/test_jevgrep_review_regressions.py`.
- `jevgrep_review_probes.py`: the one-off reproduction probes for the port
  review (was `tests/jevgrep_review_probes.py`).

To restore, move `mcp_server.py` back into `mcp/jev/grep/`, bring back
`command_mcp` and the `mcp` subparser in `cli.py` (see git history before this
commit), and register `jevgrep mcp` with each harness again.
