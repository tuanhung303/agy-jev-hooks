"""Per-question evaluation cache (port of upstream evaluation/cache.ts).

Identity hashes everything the model can see: exact query, criterion and its
version, request layout, provider endpoint, model id and reuse policy, path and
line range, chunker version, excerpt text. Threshold, budget, deadline and caps
are deliberately excluded. Never persisted: source text, full question, request
or response bodies, credentials. A corrupt or expired entry is a miss; a cache
failure disables reuse, never the search.
"""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from .policy import MAX_ROLLING_TTL_SECONDS, is_pinned_model_revision, is_rolling_model

CACHE_SCHEMA_VERSION = 3
_ENTRY_NAME = re.compile(r"^[a-f0-9]{64}\.json$")
_SHARD_NAME = re.compile(r"^[a-f0-9]{2}$")
_MAX_ENTRY_BYTES = 16_384


def evaluation_identity(input: dict) -> str:
    """Stable hash of one evaluation's inputs (canonical JSON, sorted options)."""
    provider_options = sorted((input.get("provider_options") or {}).items())
    canonical = json.dumps([
        CACHE_SCHEMA_VERSION,
        input["query"], input["path"], input["start_line"], input["end_line"], input["text"],
        input.get("label"), input["criterion_version"], input["layout_version"], input["chunker_version"],
        input["endpoint"].rstrip("/"), input["model_revision"],
        provider_options, input.get("batch_composition") or [],
    ], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_cache_entry(value) -> bool:
    return (isinstance(value, dict)
            and value.get("schema_version") == CACHE_SCHEMA_VERSION
            and isinstance(value.get("identity"), str)
            and isinstance(value.get("score"), (int, float)) and not isinstance(value.get("score"), bool)
            and 0 <= value["score"] <= 1
            and isinstance(value.get("model_revision"), str)
            and (is_pinned_model_revision(value["model_revision"]) or is_rolling_model(value["model_revision"]))
            and isinstance(value.get("layout"), str)
            and isinstance(value.get("criterion"), str)
            and isinstance(value.get("chunker"), str)
            and isinstance(value.get("created_at_ms"), int) and isinstance(value.get("expires_at_ms"), int)
            and value["created_at_ms"] >= 0
            and value["expires_at_ms"] > value["created_at_ms"])


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    failures: int = 0
    expired: int = 0
    corrupt: int = 0


class ScoreCache:
    """Bounded per-user score cache, one JSON file per entry, sharded by prefix."""

    def __init__(self, directory: str, enabled: bool, ttl_seconds: int, max_bytes: int,
                 rolling_ttl_seconds: int = MAX_ROLLING_TTL_SECONDS,
                 now: Optional[Callable[[], int]] = None):
        self.directory = directory
        self._enabled_flag = enabled
        self._ttl_seconds = ttl_seconds
        self._rolling_ttl_seconds = rolling_ttl_seconds
        self._max_bytes = max_bytes
        self._now = now or (lambda: int(time.time() * 1000))
        self.stats = CacheStats()
        try:
            os.makedirs(directory, exist_ok=True)
            self._usable = True
        except OSError:
            self._usable = False
            self.stats.failures += 1

    @property
    def enabled(self) -> bool:
        return self._enabled_flag and self._usable

    def _ttl(self, model: str) -> int:
        if is_pinned_model_revision(model):
            return self._ttl_seconds
        return (max(0, min(self._ttl_seconds, self._rolling_ttl_seconds, MAX_ROLLING_TTL_SECONDS))
                if is_rolling_model(model) else 0)

    def _path(self, identity: str) -> str:
        return os.path.join(self.directory, identity[:2], f"{identity}.json")

    def read(self, identity: str) -> Optional[float]:
        if not self.enabled or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            return None
        name = self._path(identity)
        try:
            with open(name, "r", encoding="utf-8") as handle:
                raw = handle.read(_MAX_ENTRY_BYTES + 1)
        except FileNotFoundError:
            self.stats.misses += 1
            return None
        except (OSError, ValueError):
            # ValueError covers UnicodeDecodeError: a broken entry is a miss
            self.stats.failures += 1
            self.stats.misses += 1
            return None
        try:
            entry = json.loads(raw)
        except ValueError:
            entry = None
        if (not is_cache_entry(entry) or entry["identity"] != identity
                or len(raw.encode("utf-8")) > _MAX_ENTRY_BYTES or entry["created_at_ms"] > self._now()):
            self.stats.corrupt += 1
            self.stats.misses += 1
            self._discard(name)
            return None
        # Enforce today's policy too: shortening the TTL must bite existing entries.
        ttl = self._ttl(entry["model_revision"])
        if ttl <= 0 or min(entry["expires_at_ms"], entry["created_at_ms"] + ttl * 1000) <= self._now():
            self.stats.expired += 1
            self.stats.misses += 1
            self._discard(name)
            return None
        self.stats.hits += 1
        return entry["score"]

    def write(self, identity: str, score: float, meta: dict) -> bool:
        if (not self.enabled or re.fullmatch(r"[a-f0-9]{64}", identity) is None
                or not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1
                or self._ttl(meta["model_revision"]) <= 0):
            return False
        now = self._now()
        entry = {
            "schema_version": CACHE_SCHEMA_VERSION, "identity": identity, "score": score,
            "model_revision": meta["model_revision"], "layout": meta["layout"],
            "criterion": meta["criterion"], "chunker": meta["chunker"],
            "created_at_ms": now, "expires_at_ms": now + self._ttl(meta["model_revision"]) * 1000,
        }
        raw = json.dumps(entry, separators=(",", ":")) + "\n"
        size = len(raw.encode("utf-8"))
        if not is_cache_entry(entry) or size > min(_MAX_ENTRY_BYTES, self._max_bytes):
            return False
        try:
            name = self._path(identity)
            os.makedirs(os.path.dirname(name), exist_ok=True)
            temporary = name + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(raw)
            os.replace(temporary, name)  # atomic: corruption stays local
            self.stats.writes += 1
            self._evict()
            return True
        except OSError:
            self.stats.failures += 1
            return False

    def clear(self) -> int:
        if not self._usable:
            return 0
        removed = 0
        for name in self._list_entries():
            if self._discard(name):
                removed += 1
        return removed

    def _list_entries(self) -> Dict[str, int]:
        entries: Dict[str, int] = {}
        try:
            shards = os.listdir(self.directory)
        except OSError:
            return entries
        for shard in sorted(shards):
            if _SHARD_NAME.fullmatch(shard) is None:
                continue
            shard_path = os.path.join(self.directory, shard)
            try:
                names = os.listdir(shard_path)
            except OSError:
                self.stats.failures += 1
                continue
            for name in sorted(names):
                if _ENTRY_NAME.fullmatch(name) is None or not name.startswith(shard):
                    continue
                relative = f"{shard}/{name}"
                try:
                    created = 0
                    with open(os.path.join(shard_path, name), "r", encoding="utf-8") as handle:
                        parsed = json.loads(handle.read(_MAX_ENTRY_BYTES + 1))
                    if is_cache_entry(parsed):
                        created = parsed["created_at_ms"]
                    entries[relative] = created
                except (OSError, ValueError):
                    entries[relative] = 0  # corrupt/oversized entries evict first
        return entries

    def _total_bytes(self) -> int:
        total = 0
        for name in self._list_entries():
            try:
                total += os.path.getsize(os.path.join(self.directory, name))
            except OSError:
                pass
        return total

    def _evict(self) -> None:
        if self._total_bytes() <= self._max_bytes:
            return
        oldest = sorted(self._list_entries().items(), key=lambda item: (item[1], item[0]))
        for name, _ in oldest:
            if self._total_bytes() <= self._max_bytes:
                break
            self._discard(name)

    def _discard(self, name: str) -> bool:
        try:
            os.remove(os.path.join(self.directory, name))
            return True
        except FileNotFoundError:
            return False
        except OSError:
            self.stats.failures += 1
            return False
