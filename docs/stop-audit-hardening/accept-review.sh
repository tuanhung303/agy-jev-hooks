#!/bin/bash
f=/Users/__blitzzz/Documents/GitHub/datum/tmp/way-finder/agy-honesty-hook/sol-review.md
[ -s "$f" ] || { echo "missing $f"; exit 1; }
head -1 "$f" | grep -Eq "^(keep blocking|keep blocking with fixes|switch to shadow)$" || { echo "bad verdict line"; exit 1; }
grep -q "Precision" "$f" || { echo "no Precision section"; exit 1; }
echo ok
