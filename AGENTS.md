# AGENTS.md

## Never commit sensitive project information

Scan every diff before staging. Do not commit:

- Secret values: API keys, tokens, passwords, service-account JSON, SSH private keys, TOTP seeds, `.env` contents.
- Client, company, or tenant names, customer rosters, and NDA material.
- Financial figures: revenue, payroll, invoice and contract values.
- Personal data belonging to other people.

Anonymize instead of dropping context. Replace sensitive keywords with generic placeholders (`Client A`, `Client B`, `tenant`, `example.com`, `REDACTED`) and generalize the surrounding sentence so the technical point survives without identifying anyone. Apply the same rule to prose, code, comments, file names, sample data, commit messages, and test fixtures.

Pointers are fine, values are not: a vault name, item ID, or file path where a secret lives may be committed; the secret itself may not.

Check before every commit:

```sh
git diff --cached | grep -inE 'api[_-]?key|secret|token|password|passphrase|BEGIN .*PRIVATE' 
```

If a sensitive value lands in a commit, rewrite history before pushing.

## Force-push to `origin/main` is allowed

The owner authorizes force-pushing local `main` to `origin/main` without asking again when the two have diverged. This happens when another clone pushes an older, non-anonymized copy of the history. Before each force-push:

1. Fetch, then diff the two tips: `git diff <local-tip> <remote-tip> --stat`.
2. Push only when the remote tip holds no work that local lacks. The only allowed difference is anonymization, as in the section above. When the remote has other work, stop and ask the owner.
3. Do not push a lineage that brings back a sensitive value. Scan the tree first (for example `git grep -ic <keyword> <tip>`).
4. Pin the lease to the remote SHA you inspected: `git push --force-with-lease=main:<remote-sha> origin main`. Never use a bare `--force`.

Report the old and new remote SHA after the push.
