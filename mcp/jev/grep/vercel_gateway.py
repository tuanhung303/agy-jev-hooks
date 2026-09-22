"""Jev through Vercel AI Gateway (port of upstream evaluation/vercel-gateway.ts).

Speaks the gateway evaluation-model HTTP contract directly instead of the
upstream AI SDK wrapper: POST {base}/v4/ai/evaluation-model with the gateway
protocol headers (same contract sage/jev/transport.py uses). One exchange per
scheduler attempt. Tests inject the transport and never contact Vercel.
"""
import json
from typing import Callable, Dict, Optional, Tuple

from .http import TransportResponse, bounded_request
from .jev import (RELEVANCE_CRITERION, CRITERION_VERSION, LAYOUT_VERSION, BatchEvaluation, EvaluationBatch,
                  InvalidAnswer, ProviderClient, ProviderError, ProviderUsage, TransportCancelled,
                  inspect_response_keys, question_instructions, serialize_payload)

VERCEL_JEV_MODEL = "typesafe-ai/jev"
VERCEL_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh"

GATEWAY_PROTOCOL_VERSION = "0.0.1"
GATEWAY_EVALUATION_SPEC_VERSION = "4"

FALSE_CRITERION = "This excerpt contains no concrete evidence useful for investigating the search question."


def _build_gateway_input(batch: EvaluationBatch) -> dict:
    questions = {}
    for item in batch.items:
        questions[item.id] = {
            "type": "boolean",
            "instructions": question_instructions(item),
            "criteria": {"true": RELEVANCE_CRITERION, "false": FALSE_CRITERION},
        }
    return {
        "state": {
            "search_question": batch.query,
            "criterion": RELEVANCE_CRITERION,
            "criterion_version": CRITERION_VERSION,
            "layout": LAYOUT_VERSION,
        },
        "questions": questions,
    }


def serialize_gateway_batch(batch: EvaluationBatch) -> str:
    return serialize_payload(dict(_build_gateway_input(batch), providerOptions={}))


def _usable_count(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _normalize_gateway_result(body, batch: EvaluationBatch, requested_model: str,
                              transmitted_bytes: int, duplicates, usage_ambiguous: bool) -> BatchEvaluation:
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise ProviderError("INVALID_PROVIDER_RESPONSE", "provider response contains no answer map",
                            False, True, transmitted_bytes=transmitted_bytes)
    answers = body["answers"]
    scores: Dict[str, float] = {}
    invalid = []
    requested = {item.id for item in batch.items}

    for item in batch.items:
        if item.id in duplicates:
            invalid.append(InvalidAnswer(item.id, "duplicate"))
            continue
        if item.id not in answers:
            invalid.append(InvalidAnswer(item.id, "missing"))
            continue
        answer = answers[item.id]
        if not isinstance(answer, dict) or answer.get("type") != "boolean":
            invalid.append(InvalidAnswer(item.id, "wrong_type"))
            continue
        probability = answer.get("probability")
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            invalid.append(InvalidAnswer(item.id, "wrong_type"))
        elif probability != probability or probability in (float("inf"), float("-inf")):
            invalid.append(InvalidAnswer(item.id, "not_finite"))
        elif probability < 0 or probability > 1:
            invalid.append(InvalidAnswer(item.id, "out_of_range"))
        else:
            scores[item.id] = probability
    for key in answers:
        if key not in requested:
            invalid.append(InvalidAnswer(key, "unexpected"))

    usage = None if usage_ambiguous else body.get("usage")
    usage_record = usage if isinstance(usage, dict) else {}
    response_record = body.get("response") if isinstance(body.get("response"), dict) else {}
    returned = response_record.get("modelId")
    request_id = response_record.get("id")
    return BatchEvaluation(
        scores=scores,
        invalid=tuple(invalid),
        usage=_gateway_usage(usage_record),
        requested_model=requested_model,
        returned_model=returned if isinstance(returned, str) and returned and returned != VERCEL_JEV_MODEL else None,
        transmitted_bytes=transmitted_bytes,
        request_id=request_id if isinstance(request_id, str) and request_id else None,
    )


def _gateway_usage(usage_record: dict) -> ProviderUsage:
    return ProviderUsage(_usable_count(usage_record.get("inputTokens")),
                         _usable_count(usage_record.get("outputTokens")))


class VercelGatewayAdapter(ProviderClient):
    def __init__(self, api_key: str, base_url: str = VERCEL_GATEWAY_BASE_URL,
                 transport: Optional[Callable[..., TransportResponse]] = None):
        self.model = VERCEL_JEV_MODEL
        self.endpoint = base_url.rstrip("/") + "/v4/ai/evaluation-model"
        self._api_key = api_key
        self._transport = transport or bounded_request

    def serialize_batch(self, batch: EvaluationBatch) -> str:
        return serialize_gateway_batch(batch)

    def evaluate_batch(self, batch: EvaluationBatch, is_cancelled: Optional[Callable[[], bool]] = None,
                       timeout_s: Optional[float] = None) -> BatchEvaluation:
        if is_cancelled is not None and is_cancelled():
            raise TransportCancelled()
        if not batch.items:
            raise ProviderError("INVALID_PROVIDER_RESPONSE",
                                "an evaluation batch must contain at least one excerpt", False, False)

        body = self.serialize_batch(batch)
        # Gateway may add its own protocol envelope: conservative application-level bytes.
        transmitted_bytes = len(body.encode("utf-8"))
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "jevgrep-py",
            "ai-gateway-protocol-version": GATEWAY_PROTOCOL_VERSION,
            "ai-gateway-auth-method": "api-key",
            "ai-evaluation-model-specification-version": GATEWAY_EVALUATION_SPEC_VERSION,
            "ai-model-id": self.model,
        }
        try:
            response = self._transport(self.endpoint, headers, body, timeout=timeout_s or 300.0,
                                     is_cancelled=is_cancelled)
        except TransportCancelled:
            raise ProviderError("PROVIDER_UNAVAILABLE", "AI Gateway evaluation was cancelled after dispatch",
                                False, True, transmitted_bytes=transmitted_bytes, cancelled=True) from None
        except Exception:
            cancelled = is_cancelled is not None and is_cancelled()
            raise ProviderError("PROVIDER_UNAVAILABLE",
                                "AI Gateway evaluation failed after the request may have been dispatched",
                                False, True, transmitted_bytes=transmitted_bytes, cancelled=cancelled) from None

        if response.status < 200 or response.status >= 300:
            raise _classify_gateway_status(response.status, response.headers, transmitted_bytes)
        try:
            parsed = json.loads(response.text)
        except (ValueError, TypeError):
            raise ProviderError("INVALID_PROVIDER_RESPONSE", "AI Gateway response is not valid JSON",
                                False, True, transmitted_bytes=transmitted_bytes) from None
        duplicates, usage_ambiguous = inspect_response_keys(response.text, tuple(item.id for item in batch.items))
        return _normalize_gateway_result(parsed, batch, self.model, transmitted_bytes, duplicates, usage_ambiguous)


def _classify_gateway_status(status: int, headers: Dict[str, str], transmitted: int) -> ProviderError:
    from .jev import classify_status
    if status in (401, 403, 402, 409, 429) or status >= 500:
        failure = classify_status(status, headers, transmitted)
        return ProviderError(failure.code, str(failure).replace("provider", "AI Gateway"),
                             failure.retryable, failure.ambiguous, failure.retry_after_ms,
                             failure.status, failure.transmitted_bytes)
    return ProviderError("PROVIDER_UNAVAILABLE",
                         "AI Gateway evaluation failed after the request may have been dispatched",
                         False, True, status=status, transmitted_bytes=transmitted)
