"""Freshness of selected sources before rendering (port of upstream source/freshness.ts).

A score was computed on a snapshot. If the file changed meanwhile, returning its
current lines with that score would attach a judgment to content the provider
never saw. One revalidation attempt per file; a stale file never returns; freed
space may be filled from already evaluated candidates; no paid re-evaluation.
"""
from typing import Callable, Dict, Set

from .authorization import AuthorizedRoot, UnauthorizedPathError
from .snapshot import hash_bytes

FreshnessReader = Callable[[str], bytes]


class FreshnessTracker:
    def __init__(self, read: FreshnessReader):
        self._read = read
        self._verdicts: Dict[str, str] = {}
        self._stale: Set[str] = set()
        self._checks = 0

    @property
    def unavailable_paths(self) -> Set[str]:
        return self._stale

    @property
    def check_count(self) -> int:
        return self._checks

    def check(self, relative_path: str, expected_sha256: str) -> dict:
        known = self._verdicts.get(relative_path)
        if known is not None:
            return {"path": relative_path, "verdict": known, "first_check": False}

        self._checks += 1
        try:
            verdict = "fresh" if hash_bytes(self._read(relative_path)) == expected_sha256 else "stale"
        except UnauthorizedPathError:
            verdict = "stale"
        except OSError:
            verdict = "unreadable"
        self._verdicts[relative_path] = verdict
        if verdict != "fresh":
            self._stale.add(relative_path)
        return {"path": relative_path, "verdict": verdict, "first_check": True}


def root_reader(root: AuthorizedRoot, max_file_bytes: int) -> FreshnessReader:
    def read(relative_path: str) -> bytes:
        entry = root.resolve_entry(relative_path)
        if entry.kind != "file":
            raise UnauthorizedPathError("not_regular_file", relative_path,
                                       f"{relative_path} is no longer a regular file")
        return root.read_file_bytes(entry.absolute_path, max_file_bytes)

    return read
