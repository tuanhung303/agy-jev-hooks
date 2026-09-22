"""Provider limits and local safety policies (port of upstream evaluation/policy.ts).

Estimates are not billing tokens. TypeSafe documents 64k total and 32k per
question; gateway and OpenRouter advertise 32k context. 30% headroom because the
provider tokenizer is not public.
"""
import json
import re
from typing import Optional, Tuple

from .tokens import count_reference_tokens, utf8_bytes

DEFAULT_DIRECT_MODEL = "jev-1.13.0"
MAX_ROLLING_TTL_SECONDS = 900

PINNED_MODEL = re.compile(r"^jev-\d+\.\d+\.\d+$")
OPENROUTER_REVISION = re.compile(r"^typesafe/jev-1\.13-\d{8}$")

ROLLING_MODELS = ("typesafe-ai/jev", "typesafe/jev-1.13", "jev-latest", "jev-preview")


def batch_limits(adapter: str = "typesafe-direct") -> dict:
    return {
        "total_tokens": 64_000 if adapter == "typesafe-direct" else 32_000,
        "per_question_tokens": 32_000,
        "headroom_ratio": 0.7,
        "max_items": 64,
        "max_request_bytes": 262_144,
    }


def fits_serialized_batch(body: str, limits: dict) -> bool:
    """Count the actual wire envelope: state, criteria and question keys included."""
    if utf8_bytes(body) > limits["max_request_bytes"]:
        return False
    payload = json.loads(body)
    questions = list(payload["questions"].values())
    if len(questions) > limits["max_items"]:
        return False
    if count_reference_tokens(body) > limits["total_tokens"] * limits["headroom_ratio"]:
        return False
    state_tokens = count_reference_tokens(json.dumps(payload["state"], separators=(",", ":")))
    per_question_ceiling = limits["per_question_tokens"] * limits["headroom_ratio"]
    for question in questions:
        question_tokens = count_reference_tokens(json.dumps(question, separators=(",", ":")))
        if state_tokens + question_tokens > per_question_ceiling:
            return False
    return True


def is_pinned_model_revision(model: str) -> bool:
    return PINNED_MODEL.match(model) is not None


def is_openrouter_model_revision(model: str) -> bool:
    return OPENROUTER_REVISION.match(model) is not None


def is_rolling_model(model: str) -> bool:
    return model in ROLLING_MODELS


def score_cache_policy(adapter: str, model: str, cache: dict) -> dict:
    """{'mode': pinned|rolling|disabled, 'ttl_seconds': int}"""
    if not cache.get("enabled"):
        return {"mode": "disabled", "ttl_seconds": 0}
    if adapter == "typesafe-direct" and is_pinned_model_revision(model):
        return {"mode": "pinned", "ttl_seconds": cache["ttl_seconds"]}
    rolling = (model == "typesafe-ai/jev" if adapter == "vercel-ai-gateway"
               else model == "typesafe/jev-1.13" if adapter == "openrouter"
               else model in ("jev-latest", "jev-preview"))
    ttl_seconds = min(cache["ttl_seconds"], cache.get("rolling_ttl_seconds", MAX_ROLLING_TTL_SECONDS),
                      MAX_ROLLING_TTL_SECONDS)
    return {"mode": "rolling", "ttl_seconds": ttl_seconds} if rolling and ttl_seconds > 0 \
        else {"mode": "disabled", "ttl_seconds": 0}


def estimate_cost_usd(tokens: int, price_per_million: Optional[float]) -> Optional[int | float]:
    """Round up to integer nanodollars; None when no qualified pricing record exists."""
    if price_per_million is None:
        return None
    rate = round(price_per_million * 1_000_000_000)
    return -(-(tokens * rate) // 1_000_000) / 1_000_000_000
