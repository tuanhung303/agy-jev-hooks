"""Deterministic inventory of the eligible scope (port of upstream source/inventory.ts).

One question: which files may this search read. Identical answers for every
question about the same working tree; never ranks, never executes repository
code. Exclusion order: root/scope, fixed administrative, operator deny globs,
gitignore hierarchy plus narrowing .jevgrepignore, dependency/build/generated/
minified artifacts and the size limit. Extensions never decide eligibility;
content-based exclusions belong to prepare.py.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .authorization import AuthorizedRoot, UnauthorizedPathError
from .ignore_rules import IgnoreFile, is_ignored, parse_ignore_file

ADMINISTRATIVE_DIRECTORIES = {".git", ".hg", ".svn", ".jj"}
CREDENTIAL_DIRECTORIES = {".ssh", ".gnupg", ".aws", ".kube", ".docker", "secrets"}
DEPENDENCY_DIRECTORIES = {"node_modules", "bower_components", "vendor", ".pnpm-store", ".yarn"}
BUILD_DIRECTORIES = {"dist", "build", "out", "coverage", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", ".output"}
GENERATED_DIRECTORIES = {"generated", "__generated__"}

CREDENTIAL_FILES = {".npmrc", ".yarnrc", ".yarnrc.yml", ".netrc", "_netrc", ".pypirc", ".dockercfg",
                    "credentials", "credentials.json", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
CREDENTIAL_EXTENSIONS = {".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".asc", ".ppk"}
GENERATED_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "bun.lockb",
                   "composer.lock"}
MINIFIED_NAME = re.compile(r"\.min\.(js|mjs|cjs|css)$|\.bundle\.js$", re.IGNORECASE)
GENERATED_NAME = re.compile(r"\.(generated|gen)\.[A-Za-z0-9]+$|\.d\.ts$", re.IGNORECASE)

_LITERAL_ESCAPE = re.compile(r"[.*+?^${}()|[\]\\]")


def _extension_of(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _is_env_family(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")


def _compile_deny_glob(glob: str) -> "re.Pattern[str]":
    regex = "^"
    index = 0
    while index < len(glob):
        character = glob[index]
        if character == "*":
            if glob[index + 1:index + 2] == "*":
                if glob[index + 2:index + 3] == "/":
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
        regex += _LITERAL_ESCAPE.sub(r"\\\g<0>", character)
        index += 1
    return re.compile(regex + "(?:/.*)?$")


@dataclass(frozen=True)
class InventoryEntry:
    relative_path: str
    absolute_path: str
    size_bytes: int


@dataclass(frozen=True)
class ExcludedEntry:
    relative_path: str
    reason: str
    is_directory: bool


@dataclass
class InventoryResult:
    files: List[InventoryEntry]
    discovered: int
    excluded: List[ExcludedEntry]
    excluded_by_reason: Dict[str, int]
    excluded_directories: List[ExcludedEntry]
    complete: bool
    traversal_errors: int


@dataclass(frozen=True)
class InventoryOptions:
    respect_gitignore: bool
    max_file_bytes: int
    extra_deny_globs: Tuple[str, ...] = ()
    should_stop: Optional[Callable[[], bool]] = None


class EligibilityRules:
    def __init__(self, options: InventoryOptions):
        self._deny_matchers = [_compile_deny_glob(glob) for glob in options.extra_deny_globs]
        self._max_file_bytes = options.max_file_bytes

    def directory_exclusion(self, name: str, relative_path: str) -> Optional[str]:
        if name in ADMINISTRATIVE_DIRECTORIES:
            return "administrative"
        if name in CREDENTIAL_DIRECTORIES:
            return "credential_file"
        # `src/private` and `src/private/**` both deny the directory: prune, never walk.
        if any(matcher.match(relative_path) or matcher.match(relative_path + "/") for matcher in self._deny_matchers):
            return "operator_denied"
        if name in DEPENDENCY_DIRECTORIES:
            return "dependency"
        if name in BUILD_DIRECTORIES:
            return "build_output"
        if name in GENERATED_DIRECTORIES:
            return "generated"
        return None

    def file_exclusion(self, name: str, relative_path: str, size_bytes: int) -> Optional[str]:
        if _is_env_family(name) or name in CREDENTIAL_FILES or _extension_of(name) in CREDENTIAL_EXTENSIONS:
            return "credential_file"
        if any(matcher.match(relative_path) for matcher in self._deny_matchers):
            return "operator_denied"
        if name in GENERATED_FILES:
            return "generated"
        if MINIFIED_NAME.search(name):
            return "minified"
        if GENERATED_NAME.search(name):
            return "generated"
        if size_bytes == 0:
            return "empty"
        if size_bytes > self._max_file_bytes:
            return "file_too_large"
        return None


@dataclass
class _Accumulator:
    files: List[InventoryEntry] = field(default_factory=list)
    excluded: List[ExcludedEntry] = field(default_factory=list)
    excluded_directories: List[ExcludedEntry] = field(default_factory=list)
    seen: set = field(default_factory=set)
    discovered: int = 0
    traversal_errors: int = 0
    complete: bool = True


def _join_relative(directory: str, name: str) -> str:
    return name if directory == "" else f"{directory}/{name}"


def _excluded_ancestor(rules: EligibilityRules, stack, relative_path: str, is_directory: bool):
    """Reason an explicit scope entry is refused through its ancestors, or None.

    A narrowed scope must not widen eligibility: every ancestor directory and
    the directory itself go through the same exclusion and ignore policy the
    walk applies (administrative, credential, operator, ignore rules).
    """
    segments = relative_path.split("/")
    chain = [("/".join(segments[:index]), True) for index in range(1, len(segments))]
    if is_directory:
        chain.append((relative_path, True))
    for directory, _ in chain:
        name = directory.rsplit("/", 1)[-1]
        reason = rules.directory_exclusion(name, directory)
        if reason is not None:
            return reason
        decision = is_ignored(tuple(stack), directory, True)
        if decision.ignored:
            return "jevgrepignored" if decision.narrowing else "gitignored"
    return None


def _read_ignore_file(root: AuthorizedRoot, relative_directory: str, file_name: str,
                      narrowing_only: bool, max_bytes: int) -> Optional[IgnoreFile]:
    try:
        entry = root.resolve_entry(_join_relative(relative_directory, file_name))
    except UnauthorizedPathError as cause:
        if cause.refusal == "missing":
            return None
        raise
    # Once observed, a disappearing policy is a failed scan, not an absent policy.
    text = root.read_file_bytes(entry.absolute_path, max_bytes).decode("utf-8")
    return parse_ignore_file(text, relative_directory, narrowing_only)


def _directory_ignore_files(root: AuthorizedRoot, relative_directory: str,
                            options: InventoryOptions) -> List[IgnoreFile]:
    found: List[IgnoreFile] = []
    if options.respect_gitignore:
        gitignore = _read_ignore_file(root, relative_directory, ".gitignore", False, options.max_file_bytes)
        if gitignore is not None:
            found.append(gitignore)
    jevgrepignore = _read_ignore_file(root, relative_directory, ".jevgrepignore", True, options.max_file_bytes)
    if jevgrepignore is not None:
        found.append(jevgrepignore)
    return found


def _ignore_stack_for(root: AuthorizedRoot, relative_directory: str, options: InventoryOptions) -> List[IgnoreFile]:
    stack: List[IgnoreFile] = []
    segments = [] if relative_directory == "" else relative_directory.split("/")
    relative = ""
    for index in range(len(segments) + 1):
        if index > 0:
            relative = _join_relative(relative, segments[index - 1])
        stack.extend(_directory_ignore_files(root, relative, options))
    return stack


def _consider_file(root: AuthorizedRoot, accumulator: _Accumulator, rules: EligibilityRules,
                   stack: List[IgnoreFile], name: str, relative_path: str, absolute_path: str,
                   size_bytes: int) -> None:
    try:
        identity = root.identity_key(absolute_path)
    except UnauthorizedPathError:
        accumulator.discovered += 1
        accumulator.complete = False
        accumulator.traversal_errors += 1
        return
    if identity in accumulator.seen:
        return
    accumulator.seen.add(identity)
    accumulator.discovered += 1

    named = rules.file_exclusion(name, relative_path, size_bytes)
    if named is not None:
        accumulator.excluded.append(ExcludedEntry(relative_path, named, False))
        return
    ignored = is_ignored(tuple(stack), relative_path, False)
    if ignored.ignored:
        accumulator.excluded.append(ExcludedEntry(relative_path,
                                                 "jevgrepignored" if ignored.narrowing else "gitignored", False))
        return
    accumulator.files.append(InventoryEntry(relative_path, absolute_path, size_bytes))


def _walk_directory(root: AuthorizedRoot, accumulator: _Accumulator, rules: EligibilityRules,
                    inherited: List[IgnoreFile], options: InventoryOptions,
                    relative_directory: str) -> None:
    if options.should_stop is not None and options.should_stop():
        accumulator.complete = False
        return

    try:
        entries = root.read_directory(relative_directory or ".")
    except UnauthorizedPathError as cause:
        if cause.refusal == "link":
            accumulator.excluded_directories.append(ExcludedEntry(relative_directory, "link", True))
            return
        accumulator.traversal_errors += 1
        accumulator.complete = False
        return

    try:
        stack = inherited if relative_directory == "" else inherited + _directory_ignore_files(
            root, relative_directory, options)
    except UnauthorizedPathError:
        accumulator.traversal_errors += 1
        accumulator.complete = False
        return

    for entry in sorted(entries, key=lambda item: item.name):  # deterministic, not filesystem order
        if options.should_stop is not None and options.should_stop():
            accumulator.complete = False
            return
        relative_path = _join_relative(relative_directory, entry.name)
        absolute_path = entry.path

        if entry.is_symlink():
            accumulator.discovered += 1
            accumulator.excluded.append(ExcludedEntry(relative_path, "link", False))
            continue
        if entry.is_dir(follow_symlinks=False):
            excluded = rules.directory_exclusion(entry.name, relative_path)
            if excluded is not None:
                accumulator.excluded_directories.append(ExcludedEntry(relative_path, excluded, True))
                continue
            ignored_directory = is_ignored(tuple(stack), relative_path, True)
            if ignored_directory.ignored:
                accumulator.excluded_directories.append(ExcludedEntry(
                    relative_path, "jevgrepignored" if ignored_directory.narrowing else "gitignored", True))
                continue
            _walk_directory(root, accumulator, rules, stack, options, relative_path)
            continue
        if not entry.is_file(follow_symlinks=False):
            accumulator.discovered += 1
            accumulator.excluded.append(ExcludedEntry(relative_path, "not_regular_file", False))
            continue

        try:
            size_bytes = root.resolve_entry(relative_path).size_bytes
        except UnauthorizedPathError as cause:
            accumulator.discovered += 1
            if cause.refusal in ("missing", "changed", "unavailable"):
                accumulator.complete = False
            reason = "link" if cause.refusal == "link" else "not_regular_file"
            accumulator.excluded.append(ExcludedEntry(relative_path, reason, False))
            continue
        _consider_file(root, accumulator, rules, stack, entry.name, relative_path, absolute_path, size_bytes)


def inventory_scope(root: AuthorizedRoot, scope: Tuple[str, ...], options: InventoryOptions) -> InventoryResult:
    rules = EligibilityRules(options)
    accumulator = _Accumulator()

    for requested in scope:
        if options.should_stop is not None and options.should_stop():
            accumulator.complete = False
            break
        try:
            resolved = root.resolve_entry(requested)
        except UnauthorizedPathError as cause:
            reason = ("link" if cause.refusal == "link"
                      else "outside_root" if cause.refusal == "outside_root" else "not_regular_file")
            accumulator.excluded.append(ExcludedEntry(requested, reason, False))
            accumulator.discovered += 1
            if cause.refusal in ("missing", "changed", "unavailable"):
                accumulator.complete = False
            continue

        if resolved.kind == "file":
            name = resolved.relative_path.rsplit("/", 1)[-1]
            parent = resolved.relative_path.rsplit("/", 1)[0] if "/" in resolved.relative_path else ""
            try:
                stack = _ignore_stack_for(root, parent, options)
            except UnauthorizedPathError:
                accumulator.complete = False
                accumulator.traversal_errors += 1
                continue
            reason = _excluded_ancestor(rules, stack, resolved.relative_path, False)
            if reason is not None:
                accumulator.excluded.append(ExcludedEntry(resolved.relative_path, reason, False))
                continue
            _consider_file(root, accumulator, rules, stack, name, resolved.relative_path,
                           resolved.absolute_path, resolved.size_bytes)
            continue

        relative_directory = "" if resolved.relative_path == "." else resolved.relative_path
        try:
            inherited = _ignore_stack_for(root, relative_directory, options)
        except UnauthorizedPathError:
            accumulator.complete = False
            accumulator.traversal_errors += 1
            continue
        reason = _excluded_ancestor(rules, inherited, resolved.relative_path or ".", True)
        if reason is not None:
            accumulator.excluded.append(ExcludedEntry(resolved.relative_path, reason, True))
            continue
        _walk_directory(root, accumulator, rules, inherited, options, relative_directory)

    accumulator.files.sort(key=lambda item: item.relative_path)
    accumulator.excluded.sort(key=lambda item: item.relative_path)
    excluded_by_reason: Dict[str, int] = {}
    for item in accumulator.excluded:
        excluded_by_reason[item.reason] = excluded_by_reason.get(item.reason, 0) + 1
    return InventoryResult(accumulator.files, accumulator.discovered, accumulator.excluded,
                           excluded_by_reason, accumulator.excluded_directories,
                           accumulator.complete, accumulator.traversal_errors)
