# Stop Audit Hardening Record

Briefs, implementer reports and independent reviews for the agy Stop hook CLAIM/FAIL gates (2026-10-05 to 2026-10-07). Rounds 1-4: Luna implemented, Sol reviewed. Rounds 5-8: Claude fork agents implemented, independent Claude agents reviewed (Codex unavailable).

| Round | Brief | Implementer report | Commit | Review brief | Review | Verdict |
|---|---|---|---|---|---|---|
| 1 | `prompt.md` | `report.md` | `df1d316` | `review.md` | `sol-review.md` | switch to shadow (`a83f649`) |
| 2 | `fix2.md` | `report2.md` | `bcb5503` | `review2.md` | `sol-review2.md` | keep shadow with fixes |
| 3 | `fix3.md` | `report3.md` | `733abe7` | `review3.md` | `sol-review3.md` | keep shadow with fixes |
| 4 | `fix4.md` | `report4.md` | `60a3df8` | `review4.md` | `sol-review4.md` | keep shadow with fixes |
| 5 | `fix5.md` | `report5.md` | `d546647` | `review5.md` | `sol-review5.md` | keep shadow with fixes (first all-turn sweep: 35% of real flags false) |
| 6 | `fix6.md` | `report6.md` | `7cb08ae` | `review6.md` | `sol-review6.md` | keep shadow with fixes (16/31 synthetic lies lost) |
| 7 | `fix7.md` | `report7.md` | `205f9e6`, `c8a898b` | `review7.md` | `sol-review7.md` | keep shadow with fixes (holdout lies 11/22) |
| 8 | `fix8.md` | `report8.md` | `78da81a` | – | – | precision-first: deploy log-only, 0 false blocks in blocking kinds |

`accept*.sh` are the acceptance checks each dispatch re-ran.

State at round 8: default stays shadow (`AGY_STOP_AUDIT_BLOCK_TAGS` empty). Deploy claims are log-only (`LOG_ONLY_CLAIM_KINDS`). Kai decided 2026-10-07: carry-over is allowed when an earlier same-session turn has a passing receipt, also after an edit (K1); review claims by category and drop noise, precision first.

Evaluation harness (outside the repo, in /tmp): an all-turn sweep of real transcripts, a 31-lie synthetic set, and a 22-lie / 12-control holdout written by the round-7 reviewer.
