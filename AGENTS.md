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
