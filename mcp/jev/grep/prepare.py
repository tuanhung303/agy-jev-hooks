"""Preparation: inventory to snapshots and fragments (port of upstream source/prepare.ts).

First stage allowed to read bytes, so it applies the content exclusions: invalid
encodings, binary content, empty/whitespace-only files, credential patterns and
lines no legal fragment can hold. A credential match quarantines the whole file;
text is never "cleaned", a redacted file would still have left the machine.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .authorization import AuthorizedRoot, UnauthorizedPathError
from .chunker import PreparedFragment, chunk_snapshot
from .inventory import InventoryOptions, InventoryResult, inventory_scope
from .snapshot import SnapshotError, SourceSnapshot, create_snapshot

# Quarantine patterns: reduce accidental disclosure; cannot prove absence of secrets.
CREDENTIAL_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe_secret_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("assigned_secret", re.compile(r"\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']",
                                   re.IGNORECASE)),
)


def find_credential_pattern(text: str) -> Optional[str]:
    for name, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return name
    return None


@dataclass(frozen=True)
class PreparedFile:
    snapshot: SourceSnapshot
    fragments: Tuple[PreparedFragment, ...]
    strategy: str


@dataclass(frozen=True)
class PreparationExclusion:
    relative_path: str
    reason: str
    detail: Optional[str] = None


@dataclass
class PreparedScope:
    inventory: InventoryResult
    files: List[PreparedFile]
    fragments: List[PreparedFragment]
    excluded: List[PreparationExclusion]
    unreadable: int
    prepared_bytes: int
    complete: bool


@dataclass(frozen=True)
class PreparationLimits:
    prepared_source_bytes: Optional[int] = None
    candidate_files: Optional[int] = None
    fragments: Optional[int] = None


NO_PREPARATION_LIMITS = PreparationLimits()


@dataclass(frozen=True)
class PrepareOptions:
    inventory: InventoryOptions
    window_limits: Optional[dict] = None
    limits: PreparationLimits = NO_PREPARATION_LIMITS
    should_stop: Optional[Callable[[], bool]] = None


def prepare_scope(root: AuthorizedRoot, scope: Tuple[str, ...], options: PrepareOptions) -> PreparedScope:
    """Order of fragments: normalized path order, then increasing start line."""
    inventory = inventory_scope(root, scope, options.inventory)
    limits = options.limits

    files: List[PreparedFile] = []
    fragments: List[PreparedFragment] = []
    excluded: List[PreparationExclusion] = []
    unreadable = 0
    prepared_bytes = 0
    complete = inventory.complete

    for entry in inventory.files:
        if options.should_stop is not None and options.should_stop():
            complete = False
            break
        if limits.candidate_files is not None and len(files) >= limits.candidate_files:
            complete = False
            break

        try:
            data = root.read_file_bytes(entry.absolute_path, options.inventory.max_file_bytes)
        except UnauthorizedPathError as cause:
            if cause.refusal in ("changed", "unavailable", "missing"):
                unreadable += 1
                complete = False
                continue
            excluded.append(PreparationExclusion(entry.relative_path, {
                "link": "link", "too_large": "file_too_large",
            }.get(cause.refusal, "not_regular_file")))
            continue

        if limits.prepared_source_bytes is not None and prepared_bytes + len(data) > limits.prepared_source_bytes:
            complete = False
            break

        try:
            snapshot = create_snapshot(entry.relative_path, entry.absolute_path, data)
        except SnapshotError as cause:
            excluded.append(PreparationExclusion(entry.relative_path, cause.refusal))
            continue

        if snapshot.is_blank():
            excluded.append(PreparationExclusion(entry.relative_path,
                                                 "empty" if snapshot.byte_length == 0 else "whitespace_only"))
            continue
        credential = find_credential_pattern(snapshot.text)
        if credential is not None:
            excluded.append(PreparationExclusion(entry.relative_path, "credential_pattern", credential))
            continue

        chunked = chunk_snapshot(snapshot, options.window_limits)
        if not isinstance(chunked, dict):
            excluded.append(PreparationExclusion(entry.relative_path, "unsupported_long_line", f"line {chunked.line}"))
            continue
        if limits.fragments is not None and len(fragments) + len(chunked["fragments"]) > limits.fragments:
            complete = False
            break

        files.append(PreparedFile(snapshot, tuple(chunked["fragments"]), chunked["strategy"]))
        fragments.extend(chunked["fragments"])
        prepared_bytes += len(data)

    return PreparedScope(inventory, files, fragments, excluded, unreadable, prepared_bytes,
                         complete and inventory.complete)


def exclusion_counts(prepared: PreparedScope) -> Dict[str, int]:
    counts = dict(prepared.inventory.excluded_by_reason)
    for item in prepared.excluded:
        counts[item.reason] = counts.get(item.reason, 0) + 1
    return counts
