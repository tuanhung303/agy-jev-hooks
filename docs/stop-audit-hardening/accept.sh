#!/bin/bash
set -u
R=/Users/__blitzzz/Documents/GitHub/agy-jev-hooks
cd $R || exit 1
[ "$(git branch --show-current)" = main ] || { echo "not on main"; exit 1; }
git status --porcelain | grep -v '^?? demo-video/.env$' | grep -q . && { echo "dirty tree"; git status --short; exit 1; }
uv run pytest -q tests 2>&1 | tail -3
uv run pytest -q tests >/dev/null 2>&1 || { echo "pytest failed"; exit 1; }
diff -q hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py || { echo "installed copy differs"; exit 1; }
grep -q "AGY_STOP_AUDIT_BLOCK_TAGS" hooks/agy-stop-audit.py || { echo "no BLOCK_TAGS"; exit 1; }
grep -q "WOULD_CLAIM_BG" hooks/agy-stop-audit.py || { echo "no WOULD_CLAIM_BG"; exit 1; }
f=/Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report.md
[ -s "$f" ] || { echo "no report"; exit 1; }
for s in "Precision" "Live test"; do grep -qi "$s" "$f" || { echo "report lacks $s"; exit 1; }; done
echo ok
