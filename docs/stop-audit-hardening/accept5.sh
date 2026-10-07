#!/bin/bash
R=/Users/__blitzzz/Documents/GitHub/agy-jev-hooks
cd $R || exit 1
[ "$(git branch --show-current)" = main ] || { echo "not on main"; exit 1; }
git status --porcelain | grep -v '^?? demo-video/.env$' | grep -q . && { echo "dirty tree"; git status --short; exit 1; }
[ "$(git rev-parse HEAD)" != "$(git rev-parse 10860b2)" ] || { echo "no new commit"; exit 1; }
uv run pytest -q tests 2>&1 | tail -1
uv run pytest -q tests >/dev/null 2>&1 || { echo "pytest failed"; exit 1; }
diff -q hooks/agy-stop-audit.py ~/.config/agy/agy-stop-audit.py || { echo "installed copy differs"; exit 1; }
grep -q 'AGY_STOP_AUDIT_BLOCK_TAGS", ""' hooks/agy-stop-audit.py || { echo "default no longer shadow"; exit 1; }
grep -q "exited with code" sage/*.py || { echo "native exit header not handled"; exit 1; }
f=/Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/report5.md
[ -s "$f" ] || { echo "no report5"; exit 1; }
for i in 1 2 3 4 5 6 7 8; do grep -Eq "^\| *(Finding )?$i\b" "$f" || { echo "finding $i row missing"; exit 1; }; done
grep -qi "live test" "$f" || { echo "no Live test section"; exit 1; }
echo ok
