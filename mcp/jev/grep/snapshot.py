"""Source snapshots and exact source references (port of upstream source/snapshot.ts).

A snapshot is one eligible file's bytes, read once, hashed once, indexed by line.
Every returned excerpt is a contiguous slice: nothing is reprinted or normalized.
Lines are 1-based and inclusive; CRLF is one ending; a terminal newline does not
invent an extra line; a BOM is preserved in bytes and text.
"""
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, List, Tuple

from .tokens import count_reference_tokens


class SnapshotError(Exception):
    def __init__(self, refusal: str, detail: str):
        super().__init__(f"{refusal}: {detail}")
        self.refusal = refusal  # 'binary' | 'unsupported_encoding'


@dataclass(frozen=True)
class LineSlice:
    text: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    byte_length: int


class SourceSnapshot:
    def __init__(self, relative_path: str, absolute_path: str, data: bytes, text: str,
                 line_starts: List[int], line_starts_bytes: List[int], line_tokens: List[int], sha256: str):
        self.relative_path = relative_path
        self.absolute_path = absolute_path
        self.bytes = data
        self.text = text
        self.sha256 = sha256
        self._line_starts = line_starts  # str offsets, one per line
        self._line_starts_bytes = line_starts_bytes
        self._line_tokens = line_tokens
        self.line_count = len(line_starts)
        self.has_bom = data[:3] == b"\xef\xbb\xbf"
        self.endswith_newline = text.endswith("\n")
        self._blank_lines: List[bool] | None = None

    @property
    def byte_length(self) -> int:
        return len(self.bytes)

    def _assert_line(self, line: int, label: str) -> None:
        if not isinstance(line, int) or line < 1 or line > self.line_count:
            raise ValueError(f"{label} {line} is outside 1..{self.line_count} for {self.relative_path}")

    def line_start(self, line: int) -> int:
        self._assert_line(line, "line")
        return self._line_starts[line - 1]

    def line_end(self, line: int) -> int:
        self._assert_line(line, "line")
        return len(self.text) if line == self.line_count else self._line_starts[line]

    def line_start_byte(self, line: int) -> int:
        self._assert_line(line, "line")
        return self._line_starts_bytes[line - 1]

    def line_end_byte(self, line: int) -> int:
        self._assert_line(line, "line")
        return len(self.bytes) if line == self.line_count else self._line_starts_bytes[line]

    def line_tokens(self, line: int) -> int:
        self._assert_line(line, "line")
        return self._line_tokens[line - 1]

    def range_tokens(self, start_line: int, end_line: int) -> int:
        """Upper bound: per-line sums; fine for packing (over-estimating is safe)."""
        self._assert_line(start_line, "start line")
        self._assert_line(end_line, "end line")
        return sum(self._line_tokens[start_line - 1:end_line])

    def range_bytes(self, start_line: int, end_line: int) -> int:
        return self.line_end_byte(end_line) - self.line_start_byte(start_line)

    def is_blank_line(self, line: int) -> bool:
        self._assert_line(line, "line")
        if self._blank_lines is None:
            self._blank_lines = [self.text[self.line_start(n):self.line_end(n)].strip() == ""
                                 for n in range(1, self.line_count + 1)]
        return self._blank_lines[line - 1]

    def is_blank(self) -> bool:
        return self.text.strip() == ""

    def slice_lines(self, start_line: int, end_line: int) -> LineSlice:
        """Exact contiguous slice; includes endLine's own line ending when present."""
        self._assert_line(start_line, "start line")
        self._assert_line(end_line, "end line")
        if end_line < start_line:
            raise ValueError(f"inverted line range {start_line}..{end_line} in {self.relative_path}")
        start_byte = self.line_start_byte(start_line)
        end_byte = self.line_end_byte(end_line)
        return LineSlice(
            text=self.text[self.line_start(start_line):self.line_end(end_line)],
            start_line=start_line, end_line=end_line,
            start_byte=start_byte, end_byte=end_byte, byte_length=end_byte - start_byte,
        )


def create_snapshot(relative_path: str, absolute_path: str, data: bytes,
                    count_tokens: Callable[[str], int] = count_reference_tokens) -> SourceSnapshot:
    if b"\x00" in data:
        raise SnapshotError("binary", f"{relative_path} contains NUL bytes")
    try:
        text = data.decode("utf-8")  # strict: no replacement characters
    except UnicodeDecodeError:
        raise SnapshotError("unsupported_encoding", f"{relative_path} is not valid UTF-8") from None

    # Line starts over code points and UTF-8 bytes; a terminal newline closes the last line.
    line_starts = [0]
    line_starts_bytes = [0]
    byte_offset = 0
    for index, character in enumerate(text):
        byte_offset += len(character.encode("utf-8"))
        if character == "\n":
            line_starts.append(index + 1)
            line_starts_bytes.append(byte_offset)
    if len(line_starts) > 1 and line_starts[-1] == len(text):
        line_starts.pop()
        line_starts_bytes.pop()

    line_tokens = []
    for position, start in enumerate(line_starts):
        end = line_starts[position + 1] if position + 1 < len(line_starts) else len(text)
        line_tokens.append(count_tokens(text[start:end]))

    return SourceSnapshot(relative_path, absolute_path, data, text,
                          line_starts, line_starts_bytes, line_tokens,
                          hashlib.sha256(data).hexdigest())


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
