"""Fragment preparation (port of upstream source/chunker.ts).

Upstream adds a JS/TS syntax chunker on top of line windows; this port keeps
line windows only (see NOTICE). The fragment shape is unchanged so nothing
downstream knows the difference. Fragment order is deterministic.
"""
from dataclasses import dataclass
from typing import List, Optional, Union

from .line_windows import (DEFAULT_WINDOW_LIMITS, LINE_WINDOW_CHUNKER_VERSION, FragmentWindow,
                           UnsupportedLongLine, line_windows)
from .snapshot import SourceSnapshot

CHUNKER_VERSION = LINE_WINDOW_CHUNKER_VERSION


@dataclass(frozen=True)
class PreparedFragment:
    id: str
    path: str
    sha256: str
    start_line: int
    end_line: int
    byte_start: int
    byte_end: int
    text: str
    byte_count: int
    token_count: int
    chunker: str
    classification: str = "line-window"
    label: Optional[str] = None


ChunkResult = Union[dict, UnsupportedLongLine]


def chunk_snapshot(snapshot: SourceSnapshot, limits: Optional[dict] = None) -> ChunkResult:
    """Every eligible text file goes through the window chunker."""
    windows = line_windows({"path": snapshot.relative_path, "text": snapshot.text, "sha256": snapshot.sha256},
                           limits or DEFAULT_WINDOW_LIMITS)
    if isinstance(windows, UnsupportedLongLine):
        return windows
    fragments = [PreparedFragment(
        id=window.id, path=window.path, sha256=window.sha256,
        start_line=window.start_line, end_line=window.end_line,
        byte_start=window.byte_start, byte_end=window.byte_end,
        text=window.text, byte_count=window.byte_count, token_count=window.token_count,
        chunker=window.chunker, classification="line-window", label=None,
    ) for window in windows]
    return {"kind": "fragments", "strategy": "line-window", "fallback": None, "fragments": fragments}


def uncovered_non_blank_lines(snapshot: SourceSnapshot, fragments: List[PreparedFragment]) -> List[int]:
    """Coverage invariant: non-blank lines covered by no fragment."""
    covered = [False] * (snapshot.line_count + 1)
    for fragment in fragments:
        for line in range(fragment.start_line, fragment.end_line + 1):
            covered[line] = True
    return [line for line in range(1, snapshot.line_count + 1)
            if not covered[line] and not snapshot.is_blank_line(line)]
