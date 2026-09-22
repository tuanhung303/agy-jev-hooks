"""Line-window chunker (port of upstream source/line-windows.ts).

Contiguous line-aligned windows with bounded token, byte and line limits and
bounded overlap. Pure function over one snapshot; no filesystem behaviour.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional

from .tokens import count_reference_tokens

MAX_WINDOW_OVERLAP_LINES = 8

DEFAULT_WINDOW_LIMITS = {
    "target_tokens": 800,
    "max_tokens": 1_600,
    "max_bytes": 8 * 1_024,
    "target_lines": 80,
    "max_lines": 120,
    "overlap_lines": 8,
}

# Port chunker identity: upstream syntax chunker is gone, windows only.
LINE_WINDOW_CHUNKER_VERSION = "jevgrep-py-line-windows-1"


@dataclass(frozen=True)
class FragmentWindow:
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


@dataclass(frozen=True)
class UnsupportedLongLine:
    kind: str = "unsupported-long-line"
    line: int = 0
    reason: str = "unsupported_long_line"
    byte_count: int = 0
    token_count: Optional[int] = None


@dataclass(frozen=True)
class _LineRecord:
    number: int
    start_offset: int
    end_offset: int
    byte_start: int
    byte_end: int

    @property
    def byte_count(self) -> int:
        return self.byte_end - self.byte_start


def _read_lines(text: str) -> List[_LineRecord]:
    """Split with code point and UTF-8 boundaries; CRLF is one ending."""
    lines: List[_LineRecord] = []
    byte_offset = 0
    line_start = 0
    line_byte_start = 0

    def push_line(end_offset: int, byte_end: int) -> None:
        lines.append(_LineRecord(len(lines) + 1, line_start, end_offset, line_byte_start, byte_end))

    for index, character in enumerate(text):
        byte_offset += len(character.encode("utf-8"))
        if character == "\n":
            push_line(index + 1, byte_offset)
            line_start = index + 1
            line_byte_start = byte_offset
    if line_start < len(text):
        push_line(len(text), byte_offset)
    return lines


def _text_of(lines: List[_LineRecord], text: str, start_index: int, end_index: int) -> str:
    if start_index >= len(lines) or end_index >= len(lines):
        return ""
    return text[lines[start_index].start_offset:lines[end_index].end_offset]


def line_windows(snapshot_text: dict, limits: Optional[dict] = None,
                 count: Callable[[str], int] = count_reference_tokens):
    """Split one prepared snapshot into contiguous windows or report an oversized line."""
    limits = dict(DEFAULT_WINDOW_LIMITS, **(limits or {}))
    if (limits["target_lines"] < 1 or limits["target_tokens"] < 1 or limits["max_lines"] < 1
            or limits["max_bytes"] < 1 or limits["max_tokens"] < 1 or limits["overlap_lines"] < 0
            or limits["overlap_lines"] > MAX_WINDOW_OVERLAP_LINES):
        raise ValueError("window targets and limits must be positive, overlap 0..%d" % MAX_WINDOW_OVERLAP_LINES)

    path = snapshot_text["path"]
    text = snapshot_text["text"]
    sha256 = snapshot_text["sha256"]
    if text.strip() == "":
        return []

    lines = _read_lines(text)
    windows: List[FragmentWindow] = []
    index = 0
    while index < len(lines):
        first = lines[index]
        # Byte limit first: tokenizing an arbitrarily long word is expensive.
        if first.byte_count > limits["max_bytes"]:
            return UnsupportedLongLine(line=first.number, byte_count=first.byte_count, token_count=None)
        candidate = _text_of(lines, text, index, index)
        token_count = count(candidate)
        if token_count > limits["max_tokens"]:
            return UnsupportedLongLine(line=first.number, byte_count=first.byte_count, token_count=token_count)

        end_index = index
        byte_count = first.byte_count
        while end_index + 1 < len(lines):
            following = lines[end_index + 1]
            line_count = end_index - index + 1
            if (line_count >= limits["target_lines"] or token_count >= limits["target_tokens"]
                    or end_index + 1 - index + 1 > limits["max_lines"]
                    or byte_count + following.byte_count > limits["max_bytes"]):
                break
            expanded = _text_of(lines, text, index, end_index + 1)
            expanded_tokens = count(expanded)
            if expanded_tokens > limits["max_tokens"]:
                break
            end_index += 1
            byte_count += following.byte_count
            candidate = expanded
            token_count = expanded_tokens

        last = lines[end_index]
        window = FragmentWindow(
            id=f"{path}#L{first.number}-L{last.number}",
            path=path, sha256=sha256,
            start_line=first.number, end_line=last.number,
            byte_start=first.byte_start, byte_end=last.byte_end,
            text=candidate, byte_count=last.byte_end - first.byte_start,
            token_count=token_count, chunker=LINE_WINDOW_CHUNKER_VERSION,
        )
        if candidate.strip() != "":
            windows.append(window)
        if end_index + 1 >= len(lines):
            break
        index = max(index + 1, end_index + 1 - limits["overlap_lines"])
    return windows
