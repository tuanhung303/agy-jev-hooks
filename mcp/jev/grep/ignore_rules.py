"""gitignore / .jevgrepignore matching (port of upstream source/ignore-rules.ts).

Supported subset: comments, blank lines, escapes, `!` negation, anchoring,
directory-only trailing `/`, `*` `?` per segment, `**` across segments, `[...]`
classes. Last matching rule wins; deeper files override shallower ones.
Not supported: backslash escapes of separators, case folding, Git index state.
Uncompilable patterns are skipped, never treated as matching nothing important.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

_LITERAL_ESCAPE = re.compile(r"[.*+?^${}()|[\]\\]")


@dataclass(frozen=True)
class IgnoreRule:
    negated: bool
    directory_only: bool
    matcher: "re.Pattern[str]"
    source: str


@dataclass(frozen=True)
class IgnoreFile:
    base_directory: str  # repository-relative, '/' separated, '' = root
    rules: Tuple[IgnoreRule, ...]
    narrowing_only: bool


def _compile_pattern(pattern: str, anchored: bool) -> Optional["re.Pattern[str]"]:
    regex = "^" if anchored else "^(?:.*/)?"
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            if index + 1 >= len(pattern):
                return None
            regex += _LITERAL_ESCAPE.sub(r"\\\g<0>", pattern[index + 1])
            index += 2
            continue
        if character == "*":
            doubled = pattern[index + 1:index + 2] == "*"
            if doubled:
                if pattern[index + 2:index + 3] == "/":
                    regex += "(?:.*/)?"
                    index += 3
                else:
                    regex += ".*"
                    index += 2
                continue
            regex += "[^/]*"
            index += 1
            continue
        if character == "?":
            regex += "[^/]"
            index += 1
            continue
        if character == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                return None
            body = pattern[index + 1:close].replace("\\", "\\\\")
            regex += "[^" + body[1:] + "]" if body.startswith("!") else "[" + body + "]"
            index = close + 1
            continue
        regex += _LITERAL_ESCAPE.sub(r"\\\g<0>", character)
        index += 1
    regex += "(?:/.*)?$"
    try:
        return re.compile(regex)
    except re.error:
        return None


def parse_ignore_file(text: str, base_directory: str, narrowing_only: bool = False) -> IgnoreFile:
    rules: List[IgnoreRule] = []
    for raw_line in re.split(r"\r?\n", text):
        line = raw_line
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line = re.sub(r"(?<!\\)\s+$", "", line)  # trailing spaces insignificant unless escaped
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        if negated and narrowing_only:
            continue
        directory_only = line.endswith("/")
        if directory_only:
            line = line[:-1]
        anchored = line.startswith("/") or "/" in line[:-1]
        if line.startswith("/"):
            line = line[1:]
        if not line:
            continue
        matcher = _compile_pattern(line, anchored)
        if matcher is None:
            continue
        rules.append(IgnoreRule(negated, directory_only, matcher, raw_line.strip()))
    return IgnoreFile(base_directory, tuple(rules), narrowing_only)


@dataclass(frozen=True)
class IgnoreDecision:
    ignored: bool
    rule: Optional[str]
    narrowing: bool


def is_ignored(stack: Tuple[IgnoreFile, ...], relative_path: str, is_directory: bool) -> IgnoreDecision:
    git = IgnoreDecision(False, None, False)
    narrowed = IgnoreDecision(False, None, False)
    for file in stack:
        prefix = "" if file.base_directory == "" else file.base_directory + "/"
        if prefix and not relative_path.startswith(prefix):
            continue
        candidate = relative_path[len(prefix):]
        for rule in file.rules:
            if rule.directory_only and not is_directory:
                continue
            if rule.matcher.match(candidate):
                if file.narrowing_only:
                    # narrowing files only exclude: the veto is sticky and no
                    # later .gitignore negation can undo it
                    narrowed = IgnoreDecision(not rule.negated, rule.source, True)
                else:
                    git = IgnoreDecision(not rule.negated, rule.source, False)
    return narrowed if narrowed.ignored else git
