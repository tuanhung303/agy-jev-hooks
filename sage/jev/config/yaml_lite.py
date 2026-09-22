"""sage.jev.config.yaml_lite - Dependency-free loader for the repo's own YAML files.

PyYAML lives on the user site-packages, which disappears when hook
subprocesses isolate HOME (ensure_isolated_home, test fixtures), so the
config chain must not import yaml at runtime. This loader supports exactly
the subset used by sage/jev/jev.yaml: nested mappings, block sequences
(scalars and single-key maps), single/double quoted and plain scalars,
literal (|) and folded (>-) block scalars, single-line flow maps and
sequences, comments, and null/bool/int/float scalars. Scalar/token helpers
live in sage.jev.config.yaml_scalars.

tests/test_jev_parser.py cross-checks the real config file against
PyYAML's safe_load, so any construct outside this subset fails the suite
before it can fail a hook. Unsupported constructs raise ValueError
(fail-closed, never a silent misparse).
"""
from typing import Any, List, Optional, Tuple

from sage.jev.config.yaml_scalars import (
    fold_flow,
    looks_scalar_only,
    quote_closed,
    quote_closed_body,
    scalar,
    split_key,
    strip_comment,
    unescape_double,
)


class _Reader:
    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.i = 0

    def peek(self) -> Tuple[int, str]:
        """Next significant (indent, content) line; (-1, "") at end."""
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                self.i += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            return indent, raw.strip()
        return -1, ""

    def block_scalar(self, key_indent: int, indicator: str) -> str:
        """Consume a | or > block scalar following the current key line."""
        if indicator.endswith("+"):
            raise ValueError("chomping '+' is unsupported")
        clip = not indicator.endswith("-")
        content_lines: List[str] = []
        block_indent: Optional[int] = None
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if raw.strip() == "":
                content_lines.append("")
                self.i += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent <= key_indent:
                break
            if block_indent is None:
                block_indent = indent
            content_lines.append(raw[block_indent:])
            self.i += 1
        while content_lines and content_lines[-1] == "":
            content_lines.pop()
        if not content_lines:
            return ""
        if indicator.startswith("|"):
            return "\n".join(content_lines) + ("\n" if clip else "")
        folded = []
        for line in content_lines:
            if line == "":
                folded.append("\n")
            elif folded and folded[-1] not in ("\n", " ") and not folded[-1].endswith(" "):
                folded.append(" " + line)
            else:
                folded.append(line)
        text = "".join(folded)
        if text.endswith("\n"):
            text = text[:-1]
        return text + ("\n" if clip else "")

    def quoted_scalar(self, rest: str, key_indent: int) -> Any:
        """Multi-line single/double quoted scalar with YAML flow folding."""
        quote = rest[0]
        pieces = [rest[1:]]
        while True:
            if self.i >= len(self.lines):
                raise ValueError(f"unterminated quoted scalar: {rest[:40]!r}")
            raw = self.lines[self.i]
            if raw.strip() == "":
                pieces.append("")
                self.i += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent <= key_indent:
                raise ValueError(f"unterminated quoted scalar: {rest[:40]!r}")
            pieces.append(raw.strip())
            self.i += 1
            probe = "\n".join(pieces).rstrip()
            if probe and probe[-1] == quote and quote_closed_body(probe, quote):
                text = fold_flow(probe[:-1].split("\n"))
                return text.replace("''", "'") if quote == "'" else unescape_double(text)

    def value(self, rest: str, indent: int) -> Any:
        """Parse the value after 'key:' (rest may be empty for nested blocks)."""
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            return self.block_scalar(indent, rest)
        if rest and rest[0] in "\"'" and not quote_closed(rest):
            return self.quoted_scalar(rest, indent)
        if rest:
            stripped = strip_comment(rest)
            # indentless sequence: "- item" at the same indent as its key
            if stripped.startswith("- ") and self.i < len(self.lines):
                return self.parse_sequence(indent)
            return scalar(stripped)
        next_indent, next_content = self.peek()
        if next_indent > indent:
            return self.parse_block(indent)
        if next_indent == indent and (next_content.startswith("- ") or next_content == "-"):
            return self.parse_sequence(indent)
        return None

    def parse_block(self, parent_indent: int) -> Any:
        indent, content = self.peek()
        if indent < 0:
            return None
        if content.startswith("- ") or content == "-":
            return self.parse_sequence(indent)
        return self.parse_mapping(indent)

    def parse_sequence(self, indent: int) -> List[Any]:
        items: List[Any] = []
        while True:
            line_indent, content = self.peek()
            if line_indent != indent or not (content.startswith("- ") or content == "-"):
                break
            body = content[2:].strip() if content.startswith("- ") else ""
            if not body:
                self.i += 1
                items.append(self.value("", indent))
                continue
            if ":" in body and not (body[0] in "\"'[" and looks_scalar_only(body)):
                # inline map item like "- key: value": reparse as mapping at col of body
                item_indent = indent + 2
                self.lines[self.i] = " " * item_indent + body
                items.append(self.parse_mapping(item_indent))
            else:
                self.i += 1
                items.append(scalar(strip_comment(body)))
        return items

    def parse_mapping(self, indent: int) -> dict:
        out: dict = {}
        while True:
            line_indent, content = self.peek()
            if line_indent != indent:
                if line_indent > indent:
                    raise ValueError(f"unexpected indent at line {self.i + 1}: {content!r}")
                break
            if content.startswith("- "):
                raise ValueError(f"sequence item inside mapping at line {self.i + 1}")
            key, rest = split_key(content)
            self.i += 1
            out[key] = self.value(rest, indent)
        return out


def safe_load(text: str) -> Any:
    """Parse a YAML document from this module's supported subset."""
    if not isinstance(text, str):
        raise ValueError("yaml_lite.safe_load expects str")
    reader = _Reader(text)
    indent, content = reader.peek()
    if indent < 0:
        return None
    if content.startswith("- "):
        return reader.parse_sequence(indent)
    return reader.parse_mapping(indent)
