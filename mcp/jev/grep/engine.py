"""The shared search engine (port of upstream engine.ts).

One path for CLI and MCP alike: question, files, fragments, cache, evaluations,
selection, freshness, measured response. Invariants: a rejection dispatches
nothing; a provider failure is never a score and a missing usage is never zero;
counters follow the contract validator on every response; scope_fully_scanned
only when preparation finished and every fragment has a validated evaluation.
"""
import hashlib
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .cache import ScoreCache, evaluation_identity
from .config import ConfigurationError
from .chunker import PreparedFragment
from .contracts import (ContractValidationError, ResponseLimits, create_search_error,
                        parse_search_request)
from .freshness import FreshnessTracker, root_reader
from .jev import (BatchEvaluation, BatchItem, CRITERION_VERSION, LAYOUT_VERSION, ProviderError,
                  build_request_payload, serialize_payload)
from .lifecycle import Clock, SearchContext, SearchLogger
from .policy import (batch_limits, estimate_cost_usd, fits_serialized_batch, is_openrouter_model_revision,
                     is_pinned_model_revision, score_cache_policy, MAX_ROLLING_TTL_SECONDS)
from .prepare import PrepareOptions, PreparationLimits, PreparedScope, exclusion_counts, prepare_scope
from .inventory import InventoryOptions
from .render import (ResponseBudgetError, excerpt_budget, excerpt_cost, render_search_result)
from .scheduler import run_evaluations
from .selection import select_ranges
from .tokens import REFERENCE_COUNTER_ID, count_reference_tokens, utf8_bytes
from .authorization import UnauthorizedPathError


class RequestValidationError(Exception):
    pass


@dataclass
class Scored:
    fragment: PreparedFragment
    score: float
    from_cache: bool


@dataclass
class UsageAccount:
    attempts: int = 0
    known_input_tokens: int = 0
    reserved_input_tokens: int = 0
    unknown_usage_attempts: int = 0
    transmitted_bytes: int = 0


def estimate_batch_tokens(batch, model: str, serialize: Callable) -> int:
    return count_reference_tokens(serialize(batch))


def batch_request_bytes(batch, model: str, serialize: Callable) -> int:
    return utf8_bytes(serialize(batch))


class SearchEngine:
    def __init__(self, configuration: dict, provider=None, cache: Optional[ScoreCache] = None,
                 clock: Optional[Clock] = None, logger: Optional[SearchLogger] = None,
                 env: Optional[dict] = None):
        self._configuration = configuration
        self._provider = provider
        self._clock = clock
        self._logger = logger
        self._env = env
        self._ledger = threading.Lock()  # ponytail: one lock for check-and-reserve
        self._stale_paths: set = set()
        config = configuration["config"]
        self._cache = cache or ScoreCache(
            directory=configuration["cache_directory"],
            enabled=config["cache"]["enabled"],
            ttl_seconds=config["cache"]["ttl_seconds"],
            max_bytes=config["cache"]["max_bytes"],
            rolling_ttl_seconds=config["cache"].get("rolling_ttl_seconds", MAX_ROLLING_TTL_SECONDS),
        )

    @property
    def cache(self) -> ScoreCache:
        return self._cache

    @property
    def clock(self) -> Clock:
        return self._clock or Clock()

    def search(self, request, invocation: Optional[dict] = None) -> dict:
        """Run one search. Never throws for caller input: returns a contract outcome."""
        invocation = invocation or {}
        config = self._configuration["config"]
        context = SearchContext(
            search_id=invocation.get("search_id"),
            clock=self.clock,
            started_at_ms=invocation.get("started_at_ms"),
            deadline_ms=config["search"]["deadline_ms"],
            client_cancelled=invocation.get("cancel_event"),
            logger=self._logger or SearchLogger("silent"),
        )
        try:
            return self._run(request, context)
        except (RequestValidationError, ConfigurationError, ResponseBudgetError,
                UnauthorizedPathError, ProviderError) as cause:
            code = _error_code_of(cause)
            return {"outcome": create_search_error(code, context.search_id),
                    "measured_tokens": None, "search_id": context.search_id}
        except ContractValidationError:
            raise  # a contract failure later in the pipeline is a defect, stay loud

    def _run(self, raw_request, context: SearchContext) -> dict:
        config = self._configuration["config"]
        try:
            request = parse_search_request(raw_request, ResponseLimits(
                config["search"]["default_response_tokens"], config["search"]["max_response_tokens"]))
        except ContractValidationError as cause:
            raise RequestValidationError(str(cause)) from cause

        # The mandatory report envelope is reserved before any work that could cost money.
        available = excerpt_budget(context.search_id, request["scope"], REFERENCE_COUNTER_ID,
                                   request["max_context_tokens"])

        provider = self._provider
        if provider is None:
            from .config import resolve_credential
            from .provider import create_configured_provider
            provider = create_configured_provider(config, resolve_credential(self._configuration,
                                                                            self._env or {}))

        root = self._configuration["source_root"]
        prepared = prepare_scope(root, tuple(request["scope"]), PrepareOptions(
            inventory=InventoryOptions(
                respect_gitignore=config["source"]["respect_gitignore"],
                max_file_bytes=config["source"]["max_file_bytes"],
                extra_deny_globs=tuple(config["source"]["extra_deny_globs"]),
                should_stop=lambda: not context.can_start_work(),
            ),
            limits=_preparation_limits(config),
            should_stop=lambda: not context.can_start_work(),
        ))

        # A lost authorization cannot become permission to send an earlier partial snapshot.
        root.assert_current()

        if not prepared.inventory.complete:
            context.add_stop_reason("INVENTORY_INCOMPLETE")
        if prepared.inventory.complete and not prepared.complete:
            context.add_stop_reason("PREPARATION_LIMIT")
        for directory in prepared.inventory.excluded_directories:
            context.diagnostics.record_excluded_directory(directory.reason)

        model = provider.model
        adapter = config["provider"].get("adapter") or "typesafe-direct"
        cache_policy = score_cache_policy(adapter, model, config["cache"])
        serialize = getattr(provider, "serialize_batch", None) or (
            lambda batch: serialize_payload(build_request_payload(batch, model)))

        identities: Dict[str, str] = {}
        cached_scores: Dict[str, float] = {}
        pending: List[PreparedFragment] = []
        for fragment in prepared.fragments:
            singleton = _singleton_batch(request["query"], fragment)
            input_hash = hashlib.sha256(serialize(singleton).encode("utf-8")).hexdigest()
            identity = evaluation_identity({
                "query": request["query"], "path": fragment.path,
                "start_line": fragment.start_line, "end_line": fragment.end_line,
                "text": fragment.text, "label": fragment.label,
                "criterion_version": CRITERION_VERSION, "layout_version": LAYOUT_VERSION,
                "chunker_version": fragment.chunker, "endpoint": config["provider"]["base_url"],
                "model_revision": model, "provider_options": {"adapter": adapter,
                                                              "cache_policy": cache_policy["mode"]},
                "batch_composition": [input_hash],
            })
            identities[fragment.id] = identity
            hit = self._cache.read(identity) if cache_policy["mode"] != "disabled" else None
            if hit is not None:
                cached_scores[fragment.id] = hit
            else:
                pending.append(fragment)

        pending_batches = build_batches(pending, request["query"], {
            "model": model, "serialize": serialize, "limits": batch_limits(adapter)})
        if self._cache.stats.corrupt > 0 or self._cache.stats.failures > 0:
            context.diagnostics.record("CACHE_UNAVAILABLE", context.elapsed_ms,
                                       {"count": self._cache.stats.corrupt + self._cache.stats.failures})

        enabled_caps = {key: value for key, value in config["scan_caps"].items() if value is not None}
        plan = plan_scan(pending, request["query"], {
            "model": model, "batches": pending_batches, "serialize": serialize,
            "enabled_caps": enabled_caps, "allow_partial": request["allow_partial_scan"],
            "require_fit": config["search"]["require_fit"],
            "prepared_bytes": prepared.prepared_bytes,
            "candidate_files": len(prepared.files),
            "prepared_fragments": len(prepared.fragments),
            "price_per_million_input_tokens": _pricing(config),
        })

        usage = UsageAccount()
        by_id = {fragment.id: fragment for fragment in prepared.fragments}
        scored: List[Scored] = [Scored(by_id[fragment_id], score, True)
                                for fragment_id, score in cached_scores.items() if fragment_id in by_id]

        if plan["rejected"] or ("PREPARATION_LIMIT" in context.stop_reasons()
                                and config["search"]["require_fit"] and not request["allow_partial_scan"]):
            context.add_stop_reason("SCOPE_EXCEEDS_SCAN_BUDGET")
            return self._render(context, request, prepared, [], usage, plan, available, True)
        if plan["cap_reached"]:
            context.add_stop_reason("SCAN_CAP_REACHED")

        if plan["batches"]:

            def on_dispatch(batch) -> bool:
                try:
                    root.assert_current()
                    for path in {item.path for item in batch.items}:
                        if root.resolve_entry(path).kind != "file":
                            raise UnauthorizedPathError("not_regular_file", path,
                                                        "source type changed before dispatch")
                except UnauthorizedPathError:
                    # Authorization lost after earlier dispatches: stop disclosure but
                    # keep paid work. The refused batch becomes stale, not a rejection.
                    context.add_stop_reason("SOURCE_CHANGED")
                    context.diagnostics.record("SOURCE_CHANGED", context.elapsed_ms,
                                               {"count": len(batch.items)})
                    self._stale_paths.update(item.path for item in batch.items)
                    return False
                tokens = max(1, estimate_batch_tokens(batch, model, serialize))
                data_bytes = batch_request_bytes(batch, model, serialize)
                with self._ledger:
                    projected = {
                        "request_attempts": usage.attempts + 1,
                        "transmitted_bytes": usage.transmitted_bytes + data_bytes,
                        "estimated_input_tokens": usage.known_input_tokens + usage.reserved_input_tokens + tokens,
                        "estimated_cost_usd": estimate_cost_usd(
                            usage.known_input_tokens + usage.reserved_input_tokens + tokens, _pricing(config)),
                    }
                    for key, value in projected.items():
                        cap = enabled_caps.get(key)
                        if cap is not None and (value is None or value > cap):
                            context.add_stop_reason("SCAN_CAP_REACHED")
                            return False
                    if not context.can_start_work():
                        return False
                    usage.attempts += 1
                    usage.transmitted_bytes += data_bytes
                    usage.reserved_input_tokens += tokens
                    # Provisional unknown: released by on_scores when usage is known.
                    # An attempt abandoned past the deadline keeps both.
                    usage.unknown_usage_attempts += 1
                return True

            def on_scores(batch, evaluation: BatchEvaluation) -> None:
                for item in batch.items:
                    score = evaluation.scores.get(item.id)
                    if score is None:
                        continue
                    fragment = by_id.get(item.id)
                    if fragment is None:
                        continue
                    scored.append(Scored(fragment, score, False))
                    returned = evaluation.returned_model
                    if (cache_policy["mode"] != "disabled" and evaluation.requested_model == model
                            and (returned is None or returned == model
                                 or (cache_policy["mode"] == "rolling" and adapter == "typesafe-direct"
                                     and is_pinned_model_revision(returned))
                                 or (cache_policy["mode"] == "rolling" and adapter == "openrouter"
                                     and is_openrouter_model_revision(returned)))):
                        self._cache.write(identities[fragment.id], score, {
                            "model_revision": model, "layout": LAYOUT_VERSION,
                            "criterion": CRITERION_VERSION, "chunker": fragment.chunker,
                        })
                with self._ledger:
                    if evaluation.usage.input_tokens is None:
                        # The provisional unknown and its reservation survive: an
                        # unknown attempt may still have been billed.
                        context.add_stop_reason("USAGE_UNKNOWN")
                    else:
                        usage.unknown_usage_attempts -= 1
                        usage.reserved_input_tokens -= max(1, estimate_batch_tokens(batch, model, serialize))
                        usage.known_input_tokens += evaluation.usage.input_tokens
                        used_tokens = usage.known_input_tokens + usage.reserved_input_tokens
                        used_cost = estimate_cost_usd(used_tokens, _pricing(config))
                        if ((enabled_caps.get("estimated_input_tokens") is not None
                             and used_tokens > enabled_caps["estimated_input_tokens"])
                                or (enabled_caps.get("estimated_cost_usd") is not None
                                    and used_cost is not None and used_cost > enabled_caps["estimated_cost_usd"])):
                            context.add_stop_reason("ESTIMATE_OVERRUN")
                if evaluation.invalid:
                    context.add_stop_reason("INVALID_PROVIDER_RESPONSE")
                    context.diagnostics.record("INVALID_PROVIDER_RESPONSE", context.elapsed_ms,
                                               {"count": len(evaluation.invalid)})

            def on_failure(batch, failure: ProviderError, will_retry: bool) -> None:
                # The dispatched attempt already counts as unknown usage; the
                # reservation for it survives the failure.
                context.add_stop_reason("USAGE_UNKNOWN")
                if not will_retry and (not failure.cancelled or context.stop is None):
                    context.add_stop_reason(failure.code)
                context.diagnostics.record(failure.code, context.elapsed_ms, {"count": len(batch.items)})

            run_evaluations(provider, plan["batches"], context, {
                "concurrency": config["search"]["concurrency"],
                **({"retry": config["search"]["retry"]} if config["search"].get("retry") else {}),
                "on_dispatch": on_dispatch, "on_scores": on_scores, "on_failure": on_failure,
            })

        return self._render(context, request, prepared, scored, usage, plan, available, False)

    def _render(self, context: SearchContext, request: dict, prepared: PreparedScope,
                scored: List[Scored], usage: UsageAccount, plan: dict,
                available_tokens: int, rejected: bool) -> dict:
        config = self._configuration["config"]
        snapshots = {file.snapshot.relative_path: file.snapshot for file in prepared.files}
        freshness = FreshnessTracker(root_reader(self._configuration["source_root"],
                                                 config["source"]["max_file_bytes"]))
        unavailable = set(freshness.unavailable_paths) | self._stale_paths

        candidates = [{"fragment": entry.fragment, "score": entry.score} for entry in scored]

        def slice_lines(path: str, start_line: int, end_line: int) -> Optional[str]:
            snapshot = snapshots.get(path)
            return None if snapshot is None else snapshot.slice_lines(start_line, end_line).text

        options = {
            "threshold": config["search"]["threshold"],
            "available_tokens": available_tokens,
            "measure": excerpt_cost,
            "slice_lines": slice_lines,
            "unavailable_paths": unavailable,
        }
        selection = select_ranges(candidates, options)

        # Revalidate the files a range came from, at most once each; fill freed
        # space from already evaluated candidates. No new provider work.
        stale_files = 0
        for _ in range(len(prepared.files) + 1):
            changed = False
            for range_ in selection["ranges"]:
                verdict = freshness.check(range_["path"], range_["sha256"])
                if verdict["verdict"] != "fresh":
                    changed = True
                    stale_files += 1
                    snapshots.pop(range_["path"], None)
            if not changed:
                break
            context.add_stop_reason("SOURCE_CHANGED")
            context.diagnostics.record("SOURCE_CHANGED", context.elapsed_ms, {"count": stale_files})
            selection = select_ranges(candidates, options)

        evaluated = len(scored)
        remote_evaluated = sum(1 for entry in scored if not entry.from_cache)
        cache_reused = evaluated - remote_evaluated
        total_fragments = (len(prepared.fragments)
                           if prepared.complete and prepared.unreadable == 0 else None)
        not_evaluated = max(0, len(prepared.fragments) - evaluated)
        if (not_evaluated > 0 and not plan["cap_reached"] and not rejected and context.stop is None
                and "SCAN_CAP_REACHED" not in context.stop_reasons()):
            context.add_stop_reason("PROVIDER_UNAVAILABLE")
        if usage.unknown_usage_attempts > 0:
            # Includes attempts abandoned past the deadline whose callbacks never ran.
            context.add_stop_reason("USAGE_UNKNOWN")

        estimated_input_tokens = usage.known_input_tokens + usage.reserved_input_tokens
        estimated_cost = estimate_cost_usd(estimated_input_tokens, _pricing(config))

        report = plan["report"]
        if total_fragments is None:
            report = dict(report, planned_remote_fragments=None, estimated_first_attempt_tokens=None,
                          estimated_first_attempt_cost_usd=None, estimated_first_attempt_requests=None,
                          estimated_required_caps={key: None for key in report["enabled_caps"]})

        inputs = {
            "search_id": context.search_id,
            "scope": request["scope"],
            "inventory_complete": prepared.inventory.complete and prepared.complete,
            "files": {
                "discovered": prepared.inventory.discovered,
                "eligible": len(prepared.files),
                "excluded_by_reason": exclusion_counts(prepared),
                "unreadable": prepared.unreadable,
                "changed_before_return": len(unavailable),
            },
            "fragments": {
                "total": total_fragments,
                "remote_evaluated": remote_evaluated,
                "cache_reused": cache_reused,
                "not_evaluated": not_evaluated,
                "below_threshold": selection["below_threshold"],
                "above_threshold": selection["above_threshold"],
                "omitted_stale": selection["omitted_unavailable"],
            },
            "threshold": config["search"]["threshold"],
            "duplicate_ranges_collapsed": selection["duplicate_ranges_collapsed"],
            "usage": {
                "provider_request_attempts": usage.attempts,
                "input_tokens_known_subtotal": usage.known_input_tokens,
                "input_tokens_estimated": estimated_input_tokens,
                "estimated_cost_usd": estimated_cost,
                "reported_cost_usd": None,
                "attempts_with_unknown_usage": usage.unknown_usage_attempts,
                "transmitted_bytes": usage.transmitted_bytes,
                "elapsed_ms": context.elapsed_ms,
            },
            "preflight": report,
            "requested_tokens": request["max_context_tokens"],
            "counter_id": REFERENCE_COUNTER_ID,
            "stop_reasons": order_stop_reasons(context.stop_reasons()),
            "diagnostics_truncated": context.diagnostics.truncated,
        }
        if rejected:
            inputs["rejected"] = True

        def represented_for(ranges: List[dict]) -> int:
            return _count_represented(candidates, ranges, config["search"]["threshold"], unavailable)

        rendered = render_search_result(inputs, [] if rejected else selection["ranges"], represented_for)
        context.diagnostics.set_response_tokens_measured(rendered["measured_tokens"])
        context.logger.log("info", context.search_id, "search.complete", {
            "status": rendered["result"]["status"],
            "excerpts": len(rendered["result"]["excerpts"]),
            "response_tokens": rendered["measured_tokens"],
            "attempts": usage.attempts,
        })
        return {"outcome": rendered["result"], "measured_tokens": rendered["measured_tokens"],
                "search_id": context.search_id}


def _singleton_batch(query: str, fragment: PreparedFragment):
    from .jev import EvaluationBatch
    return EvaluationBatch(query, (BatchItem(fragment.id, fragment.path, fragment.start_line,
                                             fragment.end_line, fragment.text, fragment.label),))


def _count_represented(candidates: List[dict], ranges: List[dict], threshold: float, unavailable) -> int:
    represented = 0
    for candidate in candidates:
        fragment = candidate["fragment"]
        if candidate["score"] < threshold or fragment.path in unavailable:
            continue
        if any(range_["path"] == fragment.path and range_["sha256"] == fragment.sha256
               and range_["start_line"] <= fragment.start_line
               and range_["end_line"] >= fragment.end_line for range_ in ranges):
            represented += 1
    return represented


_STOP_REASON_ORDER = [
    "SCOPE_EXCEEDS_SCAN_BUDGET", "INVENTORY_INCOMPLETE", "PREPARATION_LIMIT", "SCAN_CAP_REACHED",
    "PROVIDER_AUTH", "PROVIDER_QUOTA", "PROVIDER_RATE_LIMIT", "PROVIDER_UNAVAILABLE",
    "INVALID_PROVIDER_RESPONSE", "USAGE_UNKNOWN", "ESTIMATE_OVERRUN", "SOURCE_CHANGED",
    "DEADLINE", "CANCELLED", "RESOURCE_EXHAUSTED",
]


def order_stop_reasons(reasons: List[str]) -> List[str]:
    """Stable presentation order: two equivalent searches report reasons identically."""
    seen = list(dict.fromkeys(reasons))
    return sorted(seen, key=lambda reason: _STOP_REASON_ORDER.index(reason)
                  if reason in _STOP_REASON_ORDER else 99)


def _pricing(config: dict) -> Optional[float]:
    pricing = config["provider"].get("pricing")
    return pricing["input_usd_per_million_tokens"] if pricing else None


def _preparation_limits(config: dict) -> "PreparationLimits":
    caps = config["scan_caps"]
    return PreparationLimits(prepared_source_bytes=caps.get("prepared_source_bytes"),
                             candidate_files=caps.get("candidate_files"),
                             fragments=caps.get("fragments"))


def enabled_caps_of(caps: dict) -> dict:
    return {key: value for key, value in caps.items() if value is not None}


def plan_scan(fragments: List[PreparedFragment], query: str, options: dict) -> dict:
    """Plan first attempts and account for enabled caps. Caps disabled by default."""
    batches = options.get("batches")
    if batches is None:
        batches = build_batches(fragments, query, {
            "model": options["model"],
            **({"serialize": options["serialize"]} if "serialize" in options else {}),
        })
    serialize = options.get("serialize") or (lambda batch: serialize_payload(
        build_request_payload(batch, options["model"])))
    estimated_tokens = sum(estimate_batch_tokens(batch, options["model"], serialize) for batch in batches)
    estimated_bytes = sum(batch_request_bytes(batch, options["model"], serialize) for batch in batches)
    estimated_cost = estimate_cost_usd(estimated_tokens, options["price_per_million_input_tokens"])
    if options["enabled_caps"].get("estimated_cost_usd") is not None and estimated_cost is None:
        raise ConfigurationError("INVALID_CONFIG", "a USD cap requires a qualified pricing record")

    required = {}
    for key in options["enabled_caps"]:
        required[key] = (estimated_cost if key == "estimated_cost_usd"
                         else estimated_tokens if key == "estimated_input_tokens"
                         else estimated_bytes if key == "transmitted_bytes"
                         else len(batches) if key == "request_attempts"
                         else options["prepared_bytes"] if key == "prepared_source_bytes"
                         else options["candidate_files"] if key == "candidate_files"
                         else options["prepared_fragments"])

    overrun = any(required[key] is not None and required[key] > cap
                  for key, cap in options["enabled_caps"].items())
    rejected = overrun and options["require_fit"] and not options["allow_partial"]

    # A partial scan admits a deterministic prefix of whole batches, never all work.
    admitted = []
    tokens = 0
    data_bytes = 0
    for batch in batches:
        next_tokens = tokens + estimate_batch_tokens(batch, options["model"], serialize)
        next_bytes = data_bytes + batch_request_bytes(batch, options["model"], serialize)
        needs = {
            "estimated_input_tokens": next_tokens,
            "transmitted_bytes": next_bytes,
            "request_attempts": len(admitted) + 1,
            "estimated_cost_usd": estimate_cost_usd(next_tokens, options["price_per_million_input_tokens"]),
            "prepared_source_bytes": options["prepared_bytes"],
            "candidate_files": options["candidate_files"],
            "fragments": options["prepared_fragments"],
        }
        if any(needs.get(key, float("inf")) > cap for key, cap in options["enabled_caps"].items()):
            break
        admitted.append(batch)
        tokens = next_tokens
        data_bytes = next_bytes

    return {
        "batches": [] if rejected else admitted,
        "rejected": rejected,
        "cap_reached": len(admitted) < len(batches),
        "report": {
            "planned_remote_fragments": len(fragments),
            "planned_cache_hits": options["prepared_fragments"] - len(fragments),
            "estimated_first_attempt_tokens": estimated_tokens,
            "estimated_first_attempt_cost_usd": estimated_cost,
            "estimated_first_attempt_requests": len(batches),
            "enabled_caps": dict(options["enabled_caps"]),
            "estimated_required_caps": required,
        },
    }


def build_batches(fragments: List[PreparedFragment], query: str, options: Optional[dict] = None) -> list:
    """Deterministic batches: path and offset order, bounded by provider limits."""
    options = options or {}
    limits = options.get("limits") or batch_limits()
    serialize = options.get("serialize") or (lambda batch: serialize_payload(
        build_request_payload(batch, options.get("model", "estimate"))))

    def fits(batch) -> bool:
        return fits_serialized_batch(serialize(batch), limits)

    ordered = sorted(fragments, key=lambda fragment: (fragment.path, fragment.start_line, fragment.end_line))
    batches = []
    items: List[BatchItem] = []

    def flush() -> None:
        nonlocal items
        if items:
            batches.append(_batch_of(query, items))
            items = []

    for fragment in ordered:
        item = BatchItem(fragment.id, fragment.path, fragment.start_line, fragment.end_line,
                         fragment.text, fragment.label)
        candidate = _batch_of(query, items + [item])
        if not fits(_batch_of(query, [item])):
            raise ConfigurationError("INVALID_REQUEST", "one question exceeds the provider context estimate")
        if items and not fits(candidate):
            flush()
        items.append(item)
    flush()
    return batches


def _batch_of(query: str, items: List[BatchItem]):
    from .jev import EvaluationBatch
    return EvaluationBatch(query, tuple(items))


def _error_code_of(cause: Exception) -> str:
    if isinstance(cause, RequestValidationError):
        return "INVALID_REQUEST"
    if isinstance(cause, ConfigurationError):
        return cause.code
    if isinstance(cause, ResponseBudgetError):
        return "RESPONSE_BUDGET_TOO_SMALL"
    if isinstance(cause, UnauthorizedPathError):
        return "UNAUTHORIZED_SCOPE"
    if isinstance(cause, ProviderError):
        return cause.code
    return "INVALID_REQUEST"
