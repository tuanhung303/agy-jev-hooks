"""Ranking, merging and budgeted selection (port of upstream response/selection.ts).

Keep valid scores at or above the threshold, rank, collapse exact duplicates,
merge overlapping or adjacent ranges of one snapshot when the union fits, skip
what does not fit without abandoning smaller candidates behind it. Ties break on
path, start line, end line, fragment id: completion order can never change the
answer. This module never reads the filesystem.
"""
from typing import Callable, Dict, List, Optional, Set, Tuple

from .chunker import PreparedFragment


def _compare(left: Tuple[PreparedFragment, float], right: Tuple[PreparedFragment, float]) -> int:
    left_fragment, left_score = left
    right_fragment, right_score = right
    if left_score != right_score:
        return 1 if right_score > left_score else -1
    for attribute in ("path",):
        if getattr(left_fragment, attribute) != getattr(right_fragment, attribute):
            return -1 if getattr(left_fragment, attribute) < getattr(right_fragment, attribute) else 1
    if left_fragment.start_line != right_fragment.start_line:
        return left_fragment.start_line - right_fragment.start_line
    if left_fragment.end_line != right_fragment.end_line:
        return left_fragment.end_line - right_fragment.end_line
    return (left_fragment.id > right_fragment.id) - (left_fragment.id < right_fragment.id)


def _touches(left, right) -> bool:
    return left["start_line"] <= right["end_line"] + 1 and right["start_line"] <= left["end_line"] + 1


def select_ranges(candidates: List[dict], options: dict) -> dict:
    """candidates: [{'fragment': PreparedFragment, 'score': float}]."""
    unavailable = options.get("unavailable_paths") or set()
    threshold = options["threshold"]
    available_tokens = options["available_tokens"]
    measure: Callable[[dict], int] = options["measure"]
    slice_lines: Callable[[str, int, int], Optional[str]] = options["slice_lines"]

    qualifying = [candidate for candidate in candidates if candidate["score"] >= threshold]
    below_threshold = len(candidates) - len(qualifying)

    # Exact duplicate ranges of one snapshot are one piece of evidence, kept once.
    by_range: Dict[Tuple[str, str, int, int], dict] = {}
    duplicate_ranges_collapsed = 0
    for candidate in qualifying:
        fragment = candidate["fragment"]
        key = (fragment.path, fragment.sha256, fragment.start_line, fragment.end_line)
        existing = by_range.get(key)
        if existing is None:
            by_range[key] = candidate
            continue
        duplicate_ranges_collapsed += 1
        if _compare((fragment, candidate["score"]), (existing["fragment"], existing["score"])) < 0:
            by_range[key] = candidate

    ranked = sorted(by_range.values(),
                    key=lambda candidate: _sort_key(candidate["fragment"], candidate["score"]))
    selected: List[dict] = []
    unsliceable: Set[str] = set()
    used_tokens = 0

    for candidate in ranked:
        fragment = candidate["fragment"]
        if fragment.path in unavailable:
            continue

        neighbours = [(range_, index) for index, range_ in enumerate(selected)
                      if range_["path"] == fragment.path and range_["sha256"] == fragment.sha256
                      and _touches(range_, {"start_line": fragment.start_line, "end_line": fragment.end_line})]

        if not neighbours:
            text = slice_lines(fragment.path, fragment.start_line, fragment.end_line)
            if text is None:
                unsliceable.add(fragment.path)
                continue
            range_ = {"path": fragment.path, "sha256": fragment.sha256,
                      "start_line": fragment.start_line, "end_line": fragment.end_line,
                      "score": candidate["score"], "text": text}
            cost = measure(range_)
            if used_tokens + cost > available_tokens:
                continue
            selected.append(range_)
            used_tokens += cost
            continue

        start_line = min([fragment.start_line] + [pair[0]["start_line"] for pair in neighbours])
        end_line = max([fragment.end_line] + [pair[0]["end_line"] for pair in neighbours])
        text = slice_lines(fragment.path, start_line, end_line)
        if text is None:
            unsliceable.add(fragment.path)
            continue
        merged = {"path": fragment.path, "sha256": fragment.sha256,
                  "start_line": start_line, "end_line": end_line,
                  "score": max([candidate["score"]] + [pair[0]["score"] for pair in neighbours]),
                  "text": text}
        replaced_cost = sum(measure(pair[0]) for pair in neighbours)
        merged_cost = measure(merged)
        if used_tokens - replaced_cost + merged_cost > available_tokens:
            continue
        indexes = {pair[1] for pair in neighbours}
        selected = [range_ for index, range_ in enumerate(selected) if index not in indexes]
        selected.append(merged)
        used_tokens = used_tokens - replaced_cost + merged_cost

    selected.sort(key=lambda range_: (-range_["score"], range_["path"], range_["start_line"]))

    represented = 0
    omitted_by_budget = 0
    omitted_unavailable = 0
    for candidate in qualifying:
        fragment = candidate["fragment"]
        if fragment.path in unavailable or fragment.path in unsliceable:
            omitted_unavailable += 1
            continue
        covered = any(range_["path"] == fragment.path and range_["sha256"] == fragment.sha256
                      and range_["start_line"] <= fragment.start_line
                      and range_["end_line"] >= fragment.end_line for range_ in selected)
        if covered:
            represented += 1
        else:
            omitted_by_budget += 1

    return {
        "ranges": selected,
        "above_threshold": len(qualifying),
        "below_threshold": below_threshold,
        "duplicate_ranges_collapsed": duplicate_ranges_collapsed,
        "represented_fragments": represented,
        "omitted_by_budget": omitted_by_budget,
        "omitted_unavailable": omitted_unavailable,
    }


def _sort_key(fragment: PreparedFragment, score: float):
    return (-score, fragment.path, fragment.start_line, fragment.end_line, fragment.id)
