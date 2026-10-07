#!/bin/bash
f=/Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review7.md
[ -s "$f" ] || { echo "missing $f"; exit 1; }
head -1 "$f" | grep -Eq "^(enable CLAIM blocking|keep shadow|keep shadow with fixes)$" || { echo "bad verdict line"; exit 1; }
grep -q "Precision" "$f" || { echo "no Precision section"; exit 1; }
echo ok
