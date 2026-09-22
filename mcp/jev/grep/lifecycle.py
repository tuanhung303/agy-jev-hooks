"""Search lifecycle, cancellation, deadline and local diagnostics (port of lifecycle.ts).

Two stops stay distinct: an internal deadline still returns flagged partial
evidence, a client cancellation must not produce a new result at all.
Diagnostics stay local and bounded: timings, counters, stable codes. Never
source text, credentials, provider bodies or the full search question.
"""
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from .contracts import CONTRACT_LIMITS

LOG_LEVELS = {"silent": 0, "error": 1, "warn": 2, "info": 3, "debug": 4}


class Clock:
    """Wall clock with an interruptible sleep."""

    @property
    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def sleep(self, delay_ms: float, stopped: Optional[Callable[[], bool]] = None) -> bool:
        """Sleep up to delay_ms; False when the stop signal fired first."""
        deadline = time.monotonic() + max(0, delay_ms / 1000)
        while time.monotonic() < deadline:
            if stopped is not None and stopped():
                return False
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return not (stopped is not None and stopped())


class SearchLogger:
    """Metadata-only logger: counters and codes, never source or question text."""

    def __init__(self, level: str = "info", write: Optional[Callable[[str], None]] = None):
        self._level = LOG_LEVELS[level]
        self._write = write or (lambda line: None)

    def log(self, level: str, search_id: str, event: str, fields: Optional[dict] = None) -> None:
        if self._level < LOG_LEVELS[level]:
            return
        rendered = " ".join(f"{key}={value}" for key, value in (fields or {}).items())
        self._write(f"jevgrep {level} search={search_id} {event}" + (f" {rendered}" if rendered else ""))


class DiagnosticsRecorder:
    """Bounded record; events past the contract limit are dropped and flagged."""

    def __init__(self, search_id: str):
        self._search_id = search_id
        self.events: List[dict] = []
        self.excluded_directories: Dict[str, int] = {}
        self.cap_lower_bounds: Dict[str, int] = {}
        self.truncated = False
        self.response_tokens: Optional[int] = None

    def record(self, code: str, elapsed_ms: int, extra: Optional[dict] = None) -> None:
        if len(self.events) >= CONTRACT_LIMITS["diagnostic_events"]:
            self.truncated = True
            return
        event = {"code": code, "elapsed_ms": max(0, round(elapsed_ms))}
        extra = extra or {}
        if extra.get("count") is not None:
            event["count"] = extra["count"]
        if extra.get("path") is not None:
            event["path"] = extra["path"]
        self.events.append(event)

    def record_excluded_directory(self, reason: str) -> None:
        self.excluded_directories[reason] = self.excluded_directories.get(reason, 0) + 1

    def set_response_tokens_measured(self, tokens: int) -> None:
        self.response_tokens = tokens


class SearchContext:
    """One search's identity, time budget, stop signal and cleanup list."""

    def __init__(self, search_id: Optional[str] = None, clock: Optional[Clock] = None,
                 started_at_ms: Optional[int] = None, deadline_ms: int = 300_000,
                 client_cancelled: Optional[threading.Event] = None,
                 logger: Optional[SearchLogger] = None):
        self.search_id = search_id or str(uuid.uuid4())
        self.clock = clock or Clock()
        self.started_at_ms = started_at_ms if started_at_ms is not None else self.clock.now_ms
        self.deadline_at_ms = self.started_at_ms + deadline_ms
        self.diagnostics = DiagnosticsRecorder(self.search_id)
        self.logger = logger or SearchLogger("silent")
        self._stop_event = threading.Event()
        self._stop: Optional[str] = None  # 'deadline' | 'client_cancelled'
        self._stop_reasons: List[str] = []
        self._client_cancelled = client_cancelled

    def is_cancelled(self) -> bool:
        """For transports: no new work once the deadline passed or the client cancelled."""
        return self.stop is not None

    @property
    def stop(self) -> Optional[str]:
        self._check_deadline()
        return self._stop

    @property
    def elapsed_ms(self) -> int:
        return max(0, self.clock.now_ms - self.started_at_ms)

    @property
    def remaining_ms(self) -> int:
        return max(0, self.deadline_at_ms - self.clock.now_ms)

    def can_start_work(self) -> bool:
        return self.stop is None

    def cancel(self) -> None:
        self._stop_now("client_cancelled")

    def add_stop_reason(self, reason: str) -> None:
        if reason not in self._stop_reasons:
            self._stop_reasons.append(reason)

    def stop_reasons(self) -> List[str]:
        self._check_deadline()
        return list(self._stop_reasons)

    def _check_deadline(self) -> None:
        self.poll_client_cancel()
        if self._stop is None and self.clock.now_ms >= self.deadline_at_ms:
            self._stop_now("deadline")

    def _stop_now(self, kind: str) -> None:
        if self._stop is not None:
            return
        self._stop = kind
        self.add_stop_reason("DEADLINE" if kind == "deadline" else "CANCELLED")
        self.diagnostics.record("DEADLINE" if kind == "deadline" else "CANCELLED", self.elapsed_ms)
        self.logger.log("info", self.search_id, "deadline" if kind == "deadline" else "cancelled",
                        {"elapsed_ms": self.elapsed_ms})
        self._stop_event.set()

    def poll_client_cancel(self) -> None:
        """Fold an external cancel event (MCP notifications/cancelled) into the stop."""
        if self._client_cancelled is not None and self._client_cancelled.is_set():
            self.cancel()
