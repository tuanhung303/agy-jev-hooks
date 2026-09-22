"""Rooted source access (port of upstream source/authorization.ts).

Every inventory, ignore-file and content read crosses this interface. Checks
narrow observable replacement races; they are not an atomic OS sandbox.
Windows reparse-point probing is dropped (see NOTICE).
"""
import os
import re
from dataclasses import dataclass
from typing import Callable, List, Tuple, TypeVar

_DEVICE_NAMES = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(\.|$)", re.IGNORECASE)
_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")

T = TypeVar("T")


class UnauthorizedPathError(Exception):
    def __init__(self, refusal: str, requested_path: str, detail: str):
        super().__init__(f"{refusal}: {detail}")
        self.refusal = refusal
        self.requested_path = requested_path


def assert_safe_relative_path(input_path: str) -> str:
    def fail() -> None:
        raise UnauthorizedPathError("unsupported_path_syntax", input_path, "unsupported relative path syntax")

    if not isinstance(input_path, str) or input_path == "" or _FORBIDDEN.search(input_path):
        fail()
    unified = input_path.replace("\\", "/")
    if unified.startswith("/"):
        fail()
    segments = [segment for segment in unified.split("/") if segment not in ("", ".")]
    for segment in segments:
        if segment == ".." or re.search(r"[. ]$", segment) or _DEVICE_NAMES.match(segment):
            fail()
    return "/".join(segments) or "."


@dataclass(frozen=True)
class ResolvedEntry:
    relative_path: str
    absolute_path: str
    kind: str  # 'file' | 'directory'
    size_bytes: int


def _fail(reason: str, path: str) -> None:
    raise UnauthorizedPathError(reason, path, f"source access refused ({reason})")


def _contained(candidate: str, root: str) -> bool:
    root_prefix = root if root.endswith(os.sep) else root + os.sep
    return candidate == root or candidate.startswith(root_prefix)


@dataclass(frozen=True)
class _CheckedEntry:
    path: str
    stat_result: os.stat_result


def _metadata(path: str) -> os.stat_result:
    try:
        stats = os.lstat(path)
    except FileNotFoundError:
        _fail("missing", path)
    except OSError:
        _fail("unavailable", path)
    if os.path.islink(path):
        _fail("link", path)
    if not os.path.isdir(path) and not os.path.isfile(path):
        _fail("not_regular_file", path)
    return stats


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev == right.st_dev and left.st_ino == right.st_ino
            and (left.st_mode & 0o170000) == (right.st_mode & 0o170000))


def _unchanged(left: os.stat_result, right: os.stat_result) -> bool:
    return (_same(left, right) and left.st_size == right.st_size
            and left.st_mtime_ns == right.st_mtime_ns and left.st_ctime_ns == right.st_ctime_ns)


def _ancestors(absolute: str) -> List[str]:
    if not os.path.isabs(absolute):
        _fail("unsupported_path_syntax", absolute)
    drive, rest = os.path.splitdrive(absolute)
    root = (drive + os.path.sep) if drive else os.path.sep
    paths = [root]
    current = root
    for part in [p for p in rest.split(os.path.sep) if p]:
        if assert_safe_relative_path(part) != part:
            _fail("unsupported_path_syntax", absolute)
        current = os.path.join(current, part)
        paths.append(current)
    return paths


def _checked_ancestors(absolute: str) -> List[_CheckedEntry]:
    chain = []
    for path in _ancestors(absolute):
        stats = _metadata(path)
        if not os.path.isdir(path):
            _fail("not_regular_file", path)
        chain.append(_CheckedEntry(path, stats))
    return chain


def _check_chain(chain: List[_CheckedEntry]) -> None:
    for expected in chain:
        if not _same(expected.stat_result, _metadata(expected.path)):
            _fail("changed", expected.path)


class AuthorizedRoot:
    """Pinned filesystem authorization for one repository root."""

    def __init__(self, path: str, anchor: List[_CheckedEntry]):
        self.path = path
        self._anchor = anchor
        self._invalidated = False

    @classmethod
    def open(cls, root_path: str) -> "AuthorizedRoot":
        # Ancestors of the root may be environmental symlinks (macOS /tmp, /var);
        # links *inside* the authorized tree stay refused. The chain is anchored on
        # the canonical path, and the requested spelling must resolve to the same
        # object before it is trusted.
        requested = os.path.abspath(root_path)
        requested_stats = _metadata(requested)
        canonical = os.path.realpath(requested)
        anchor = _checked_ancestors(canonical)
        if not _same(requested_stats, anchor[-1].stat_result):
            _fail("changed", requested)
        return cls(canonical, anchor)

    def contains(self, absolute_path: str) -> bool:
        return _contained(os.path.abspath(absolute_path), self.path)

    def relativize(self, absolute_path: str) -> str:
        absolute = os.path.abspath(absolute_path)
        if not self.contains(absolute):
            _fail("outside_root", absolute)
        relative = os.path.relpath(absolute, self.path)
        return "/".join(part for part in relative.split(os.path.sep) if part) or "."

    def identity_key(self, absolute_path: str) -> str:
        return self.resolve_entry(self.relativize(absolute_path)).absolute_path

    def assert_current(self) -> None:
        if self._invalidated:
            _fail("changed", self.path)
        try:
            _check_chain(self._anchor)
        except UnauthorizedPathError:
            self._invalidated = True
            raise

    def _checked(self, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except UnauthorizedPathError as cause:
            if any(entry.path == cause.requested_path for entry in self._anchor):
                self._invalidated = True
            else:
                try:
                    self.assert_current()
                except UnauthorizedPathError:
                    pass  # assert_current retains the failure
            raise

    def resolve_entry(self, relative_path: str) -> ResolvedEntry:
        return self._checked(lambda: self._resolve_walk(assert_safe_relative_path(relative_path)))[0]

    def _resolve_walk(self, normalized: str) -> Tuple[ResolvedEntry, List[_CheckedEntry]]:
        self.assert_current()
        chain = list(self._anchor)
        current = self.path
        parts = [] if normalized == "." else normalized.split("/")
        for index, part in enumerate(parts):
            candidate = os.path.join(current, part)
            stats = _metadata(candidate)
            if index < len(parts) - 1 and not os.path.isdir(candidate):
                _fail("not_regular_file", normalized)
            canonical = os.path.realpath(candidate)
            if not _contained(canonical, self.path):
                _fail("outside_root", normalized)
            if not _same(stats, _metadata(canonical)):
                _fail("changed", normalized)
            current = canonical
            chain.append(_CheckedEntry(current, stats))
        self._checked(lambda: _check_chain(chain))
        last = chain[-1]
        kind = "directory" if os.path.isdir(last.path) else "file"
        return ResolvedEntry(self.relativize(current), current, kind, last.stat_result.st_size), chain

    def read_directory(self, relative_path: str) -> List["os.DirEntry[str]"]:
        entry, chain = self._checked(lambda: self._resolve_walk(assert_safe_relative_path(relative_path)))
        if entry.kind != "directory":
            _fail("not_regular_file", relative_path)

        def read() -> List["os.DirEntry[str]"]:
            with os.scandir(entry.absolute_path) as scanner:
                entries = list(scanner)
            # A swap during the listing must not pass as the checked directory.
            self._checked(lambda: _check_chain(chain))
            return entries

        return self._checked(read)

    def read_file_bytes(self, absolute_path: str, max_bytes: int) -> bytes:
        """Open once, bind identity to the original leaf, read to EOF under a byte ceiling."""
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        checked, chain = self._checked(
            lambda: self._resolve_walk(assert_safe_relative_path(self.relativize(absolute_path))))
        if checked.kind != "file":
            _fail("not_regular_file", absolute_path)

        def read() -> bytes:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(checked.absolute_path, flags)
            try:
                before = os.fstat(descriptor)
                # Bind the descriptor to the leaf identity captured at walk time;
                # a swapped path then fails here instead of leaking new content.
                if not _unchanged(chain[-1].stat_result, before):
                    _fail("changed", absolute_path)
                self._checked(lambda: _check_chain(chain))
                if before.st_size > max_bytes:
                    _fail("too_large", absolute_path)
                chunks: List[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(65_536, max_bytes + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        _fail("too_large", absolute_path)
                    chunks.append(chunk)
                after = os.fstat(descriptor)
                if not _unchanged(before, after) or after.st_size != total:
                    _fail("changed", absolute_path)
                self._checked(lambda: _check_chain(chain))
                if not _unchanged(after, _metadata(checked.absolute_path)):
                    _fail("changed", absolute_path)
                return b"".join(chunks)
            finally:
                os.close(descriptor)

        return self._checked(read)
