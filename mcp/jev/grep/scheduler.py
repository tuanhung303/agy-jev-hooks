"""Bounded provider work (port of upstream evaluation/scheduler.ts).

The engine reserves every attempt before this seam sends it. Thread pool instead
of async workers; one attempt per evaluate_batch call, bounded retries with
jittered backoff, one global cooldown after a rate limit.
"""
import random as _random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable, List, Optional

from .jev import BatchEvaluation, EvaluationBatch, ProviderClient, ProviderError, TransportCancelled

DEFAULT_RETRY_POLICY = {"max_retries": 2, "base_delay_ms": 250, "max_delay_ms": 5_000, "retry_ambiguous": False}


def _is_abort(failure: BaseException) -> bool:
    return isinstance(failure, TransportCancelled) or getattr(failure, "cancelled", False)


def run_evaluations(provider: ProviderClient, batches: List[EvaluationBatch], context,
                    handlers: dict) -> None:
    retry = handlers.get("retry") or DEFAULT_RETRY_POLICY
    random = handlers.get("random") or _random.random
    terminal = threading.Event()
    cooldown_lock = threading.Lock()
    cooldown_until = [0]
    index_lock = threading.Lock()
    next_index = [0]

    def pause_until(until_ms: float) -> bool:
        while not terminal.is_set() and context.can_start_work():
            with cooldown_lock:
                effective = max(until_ms, cooldown_until[0])
            delay = effective - context.clock.now_ms
            if delay <= 0:
                return True
            if not context.clock.sleep(min(delay, context.remaining_ms),
                                      lambda: terminal.is_set() or context.stop is not None):
                return False
        return False

    def worker() -> None:
        while True:
            with index_lock:
                position = next_index[0]
                if position >= len(batches):
                    return
                next_index[0] = position + 1
            if not pause_until(0):
                return
            batch = batches[position]
            attempt = 0
            while True:
                if terminal.is_set() or not context.can_start_work():
                    return
                on_dispatch = handlers.get("on_dispatch")
                if on_dispatch is not None and on_dispatch(batch) is False:
                    terminal.set()
                    return
                try:
                    evaluation = provider.evaluate_batch(
                        batch, is_cancelled=context.is_cancelled,
                        timeout_s=max(0.05, context.remaining_ms / 1000))
                    if not context.can_start_work():
                        raise TransportCancelled()
                    handlers["on_scores"](batch, evaluation)
                    break
                except BaseException as cause:  # noqa: BLE001 - normalized below
                    if terminal.is_set() or not context.can_start_work():
                        return  # a detached failure must not mutate accounting
                    if _is_abort(cause):
                        failure = ProviderError("PROVIDER_UNAVAILABLE",
                                                "evaluation aborted after possible dispatch",
                                                False, True, cancelled=True)
                    elif isinstance(cause, ProviderError):
                        failure = cause
                    else:
                        raise
                    fatal = (failure.code in ("PROVIDER_AUTH", "PROVIDER_QUOTA")
                             or (failure.status is not None and 400 <= failure.status < 500
                                 and failure.status != 429))
                    if fatal:
                        terminal.set()
                    will_retry = (not terminal.is_set() and context.can_start_work()
                                  and not failure.cancelled and attempt < retry["max_retries"]
                                  and failure.retryable
                                  and (not failure.ambiguous or retry["retry_ambiguous"]))
                    backoff = min(retry["max_delay_ms"], retry["base_delay_ms"] * 2 ** attempt)
                    jittered = round(backoff * (0.5 + max(0.0, min(1.0, random())) * 0.5))
                    delay = max(jittered, failure.retry_after_ms or 0)
                    if failure.code == "PROVIDER_RATE_LIMIT":
                        with cooldown_lock:
                            cooldown_until[0] = max(cooldown_until[0], context.clock.now_ms + delay)
                    handlers["on_failure"](batch, failure, will_retry)
                    attempt += 1
                    if not will_retry:
                        break
                    if not pause_until(context.clock.now_ms + delay):
                        return

    count = min(max(1, handlers["concurrency"]), len(batches))
    if count <= 0:
        return
    failures: List[BaseException] = []
    executor = ThreadPoolExecutor(max_workers=count)
    futures = [executor.submit(worker) for _ in range(count)]
    # ponytail: stop waiting at the deadline plus a tiny grace; a detached
    # thread dies with its deadline-aware transport timeout and its late
    # results are discarded by the can_start_work guards. Unreconciled
    # attempts stay flagged unknown usage by the engine.
    wait_until = time.monotonic() + max(0.0, context.remaining_ms / 1000) + 0.1
    for future in futures:
        try:
            future.result(timeout=max(0.0, wait_until - time.monotonic()))
        except FuturesTimeoutError:
            terminal.set()
        except BaseException as cause:  # noqa: BLE001
            terminal.set()
            failures.append(cause)
    terminal.set()
    executor.shutdown(wait=False)
    if failures:
        raise failures[0]
