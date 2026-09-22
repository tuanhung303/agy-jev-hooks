"""sage.jev.config.yaml_scalars - Scalar/token helpers for the yaml_lite loader.

Split out of yaml_lite.py to keep both modules under the repo's 300-line
cap. Covers exactly the scalar subset used by sage/jev/jev.yaml: quoted and
plain scalars, flow maps/sequences, comment stripping, key splitting, and
YAML flow folding. Any unsupported construct raises ValueError (fail-closed,
never a silent misparse); tests/test_jev_parser.py cross-checks the loader
against PyYAML on the real config files.
"""
import re
from typing import Any, List, Tuple

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def quote_closed_body(text: str, quote: str) -> bool:
    """Probe for a rstripped joined body whose LAST char equals the quote.

    Single-quoted: odd trailing run of quotes means the final one closes
    (even run = an escaped '' pair, still inside the scalar). Double-quoted:
    the final quote closes unless an odd backslash run escapes it.
    """
    run = 0
    i = len(text) - 1
    while i >= 0 and text[i] == quote:
        run += 1
        i -= 1
    if quote == "'":
        return run % 2 == 1
    backslashes = 0
    i = len(text) - 2  # char before the closing-candidate quote
    while i >= 0 and text[i] == "\\":
        backslashes += 1
        i -= 1
    return backslashes % 2 == 0


def strip_comment(text: str) -> str:
    """Drop a trailing comment; # only counts outside quotes at depth zero."""
    quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        if quote == '"':
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quote = ""
        elif quote == "'":
            if char == "'":
                if i + 1 < len(text) and text[i + 1] == "'":  # '' escape
                    i += 2
                    continue
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def scalar(token: str) -> Any:
    token = token.strip()
    if token == "" or token == "null" or token == "~":
        return None
    if token[0] in "\"'":
        quote = token[0]
        if len(token) < 2 or token[-1] != quote:
            raise ValueError(f"unterminated quoted scalar: {token!r}")
        body = token[1:-1]
        if quote == '"':
            return (body.replace("\\n", "\n").replace("\\t", "\t")
                        .replace('\\"', '"').replace("\\\\", "\\"))
        return body.replace("''", "'")
    if token in ("true", "True", "yes"):
        return True
    if token in ("false", "False", "no"):
        return False
    if _INT_RE.match(token):
        return int(token)
    if _FLOAT_RE.match(token):
        return float(token)
    if token.startswith(("{", "[")):
        return flow(token)
    return token


def split_flow(body: str) -> List[str]:
    """Split a flow body on commas at depth zero, respecting quotes."""
    parts, buf, depth, quote = [], [], 0, ""
    for char in body:
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    if quote:
        raise ValueError(f"unterminated quote in flow: {body!r}")
    parts.append("".join(buf))
    return [p for p in parts if p.strip()]


def flow(token: str) -> Any:
    token = token.strip()
    if token.startswith("{"):
        if not token.endswith("}"):
            raise ValueError(f"unterminated flow map: {token!r}")
        out = {}
        for part in split_flow(token[1:-1]):
            if ":" not in part:
                raise ValueError(f"flow map entry lacks colon: {part!r}")
            key, _, value = part.partition(":")
            out[str(scalar(key))] = scalar(value)
        return out
    if token.startswith("["):
        if not token.endswith("]"):
            raise ValueError(f"unterminated flow seq: {token!r}")
        return [scalar(part) for part in split_flow(token[1:-1])]
    raise ValueError(f"not a flow value: {token!r}")


def quote_closed(text: str) -> bool:
    """True when a leading quote finds its closer inside this single line."""
    quote = text[0]
    i = 1
    while i < len(text):
        char = text[i]
        if quote == '"':
            if char == "\\":
                i += 2
                continue
            if char == '"':
                return True
        else:
            if char == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                return True
        i += 1
    return False


def fold_flow(lines: List[str]) -> str:
    """YAML flow-scalar folding: 1 break -> space, n breaks -> n-1 newlines.

    Trailing blank lines (blank run reaching the closing quote) encode the
    scalar's trailing newlines; PyYAML keeps them, so they are appended, not
    dropped.
    """
    while lines and lines[0] == "":
        lines.pop(0)
    if not lines:
        return ""
    out = lines[0]
    i = 1
    while i < len(lines):
        blanks = 0
        while i < len(lines) and lines[i] == "":
            blanks += 1
            i += 1
        if i >= len(lines):
            # A trailing run of K blanks encodes K-1 content newlines: the
            # final break sits immediately before the closing quote and the
            # spec folds it away (PyYAML roundtrips accordingly).
            out += "\n" * max(0, blanks - 1)
            break
        out += (" " if blanks == 0 else "\n" * blanks) + lines[i]
        i += 1
    return out


def unescape_double(body: str) -> str:
    out, i = [], 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b",
              "f": "\f", "v": "\v", "\\": "\\", '"': '"', "/": "/", "'": "'"}
    while i < len(body):
        char = body[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise ValueError("dangling escape in quoted scalar")
        esc = body[i]
        if esc in simple:
            out.append(simple[esc])
            i += 1
        elif esc == "x":
            out.append(chr(int(body[i + 1:i + 3], 16)))
            i += 3
        elif esc in ("u", "U"):
            width = 4 if esc == "u" else 8
            out.append(chr(int(body[i + 1:i + 1 + width], 16)))
            i += 1 + width
        else:
            raise ValueError(f"unsupported escape \\{esc}")
    return "".join(out)


def split_key(content: str) -> Tuple[str, str]:
    """Split 'key: rest' respecting quoted keys; returns (key, rest)."""
    if content[0] in "\"'":
        quote = content[0]
        end = content.find(quote, 1)
        while end != -1 and content[end - 1] == "\\":
            end = content.find(quote, end + 1)
        if end == -1:
            raise ValueError(f"unterminated key quote: {content!r}")
        key = content[1:end]
        rest = content[end + 1:].lstrip()
        if not rest.startswith(":"):
            raise ValueError(f"key lacks colon: {content!r}")
        return key, rest[1:].strip()
    match = re.search(r":(?:\s|$)", content)
    if match is None:
        raise ValueError(f"mapping line lacks colon: {content!r}")
    return content[:match.start()].rstrip(), content[match.end():].strip()


def looks_scalar_only(body: str) -> bool:
    """Quoted/flow bodies without a mapping colon are plain items."""
    if body[0] in "\"'":
        return True
    if body[0] in "[{":
        return ":" not in body.split(",")[0]
    return False
