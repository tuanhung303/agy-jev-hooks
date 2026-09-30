# Jev prompt bench

Measures how well Jev ranks grep matches for the grep hint hook
(`hooks/claude-grep-filter.py`), so a prompt change can be compared with what
ships. Each case in `cases.json` is a grep pattern, the user's recent prompts
(an unrelated older request, the real question, a bare "yeah okay do that"),
and the files that answer it. Every arm in `arms.py` ranks the same matches;
only the prompt differs.

```sh
.venv/bin/python bench/jev_prompt/run.py                              # all arms, 2 runs
.venv/bin/python bench/jev_prompt/run.py --arms shipped,my-idea --runs 2
```

About 20-25 s per arm per run. The searched code is a `git archive` snapshot of
this repository at the pinned commit in `run.py` (`PINNED_REF`), so the numbers
stay comparable while the repository changes; the prompt and hook code under
test are the working tree's. It sends the snapshot's matching code to Jev
through the provider of `~/.config/jevgrep/agy-jev-hooks.yaml`, with the score
cache off (the cache keys on version strings, not on prompt text). Results are
written to `~/.local/state/agy-jev-hooks/bench/`.

## Reading the numbers

- **mean AUC**: how often a known answer outscores a non-answer, averaged over
  cases (1.0 is perfect).
- **top8**: known answers inside the 8 files the hint lists; **top3**: inside
  the first three.
- Run-to-run noise is 0.01-0.03 mean AUC. A new arm is better only if it beats
  `shipped` in every run without losing top-8 hits.
- 5 cases and 10 answers, all in this repository: good for catching a
  regression or a clear gain, too small to tune fine wording against. Do not
  word a prompt around these cases' topics.

When a change ships, bump `CRITERION_VERSION` (and `LAYOUT_VERSION` for a
payload shape change) in `mcp/jev/grep/jev.py`, then add a row below.

## History (snapshot a05a4fd unless noted)

| Date | Prompt | mean AUC (2 runs) | top3 | top8 |
|---|---|---|---|---|
| 2026-09-28 | criterion-3: false = "other component or an older copy" (shipped) | 0.931 / 0.898 | 6/10, 6/10 | 10/10, 10/10 |
| 2026-09-30 | criterion-3, at most 2 snippets per file (shipped; 480 KB vs 752 KB sent) | 0.922 / 0.907 | 6/10, 6/10 | 9/10, 10/10 |
| 2026-09-30 | criterion-3, ±5 lines around matches instead of ±12 (rejected) | 0.814 / 0.827 | 5/10, 5/10 | 8/10, 9/10 |
| 2026-09-30 | criterion-3, the 2 snippets per file with the most matches instead of the first 2 (rejected; 519 KB) | 0.895 / 0.897 | 5/10, 5/10 | 9/10, 9/10 |
| 2026-09-28 | criterion-2: false = "different feature that happens to use the same word" | 0.874 / 0.886 | 4/10, 5/10 | 8/10, 9/10 |

Earlier results, measured on the live working tree before the bench existed
(not directly comparable): excerpt-only instructions with version tags in the
state 0.83-0.84; the same text restructured 0.83-0.84; direct question
(criterion-2) 0.91; adding a worked example to the criteria no gain and +7%
bytes; a one-hop "does it show the behavior" question 0.87; dropping the
`<question>` tag from the query 0.89; newest-first prompts worse than
oldest-first.
