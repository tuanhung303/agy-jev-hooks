"""Jev through OpenRouter's alpha Decisions API (port of upstream evaluation/openrouter.ts).

Not chat completions. One bounded exchange per scheduler attempt, no fallback
providers. The alpha API may change.
"""
from typing import Callable, Optional

from .http import TransportResponse, bounded_request
from .jev import (BatchEvaluation, EvaluationBatch, ProviderClient, ProviderError, TransportCancelled,
                  build_request_payload, evaluate_decision_request, serialize_payload)

OPENROUTER_JEV_MODEL = "typesafe/jev-1.13"
OPENROUTER_BASE_URL = "https://openrouter.ai"

FALSE_CRITERION = "This excerpt contains no concrete evidence useful for investigating the search question."


def serialize_openrouter_batch(batch: EvaluationBatch) -> str:
    """Exact wire payload, also used by offline inspection and search planning."""
    payload = build_request_payload(batch, OPENROUTER_JEV_MODEL)
    questions = {}
    for question_id, question in payload["questions"].items():
        questions[question_id] = dict(question, criteria={"true": question["criteria"]["true"], "false": FALSE_CRITERION})
    payload["questions"] = questions
    payload["provider"] = {"allow_fallbacks": False}
    return serialize_payload(payload)


class OpenRouterAdapter(ProviderClient):
    def __init__(self, api_key: str, base_url: str = OPENROUTER_BASE_URL,
                 transport: Optional[Callable[..., TransportResponse]] = None):
        self.model = OPENROUTER_JEV_MODEL
        self.endpoint = base_url.rstrip("/") + "/api/alpha/decisions"
        self._api_key = api_key
        self._transport = transport or bounded_request

    def serialize_batch(self, batch: EvaluationBatch) -> str:
        return serialize_openrouter_batch(batch)

    def evaluate_batch(self, batch: EvaluationBatch, is_cancelled: Optional[Callable[[], bool]] = None,
                       timeout_s: Optional[float] = None) -> BatchEvaluation:
        if is_cancelled is not None and is_cancelled():
            raise TransportCancelled()
        if not batch.items:
            raise ProviderError("INVALID_PROVIDER_RESPONSE",
                                "an evaluation batch must contain at least one excerpt", False, False)
        return evaluate_decision_request(
            self.endpoint, {
                "authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": "jevgrep-py",
            },
            self.serialize_batch(batch), batch, self.model, self._transport, is_cancelled,
            request_id_header="x-openrouter-request-id", timeout_s=timeout_s)
