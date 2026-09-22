"""Select the configured Jev transport after configuration and credential validation."""
from typing import Callable, Optional

from .jev import EvaluationBatch, ProviderClient, serialize_payload, build_request_payload
from .openrouter import OPENROUTER_JEV_MODEL, OpenRouterAdapter, serialize_openrouter_batch
from .vercel_gateway import VERCEL_JEV_MODEL, VercelGatewayAdapter, serialize_gateway_batch

ADAPTERS = ("typesafe-direct", "vercel-ai-gateway", "openrouter")


def configured_adapter(config: dict) -> str:
    return config["provider"].get("adapter") or "typesafe-direct"


def serialize_configured_batch(config: dict, batch: EvaluationBatch) -> str:
    adapter = configured_adapter(config)
    if adapter == "openrouter":
        return serialize_openrouter_batch(batch)
    if adapter == "vercel-ai-gateway":
        return serialize_gateway_batch(batch)
    return serialize_payload(build_request_payload(batch, config["provider"]["model"]))


def create_configured_provider(config: dict, api_key: str) -> ProviderClient:
    provider = config["provider"]
    adapter = configured_adapter(config)
    if adapter == "typesafe-direct":
        from .jev import JevAdapter
        return JevAdapter(provider["base_url"], provider["model"], api_key)
    if adapter == "openrouter":
        if provider["model"] != OPENROUTER_JEV_MODEL:
            raise ValueError(f"openrouter requires provider.model={OPENROUTER_JEV_MODEL}")
        return OpenRouterAdapter(api_key, provider["base_url"])
    if provider["model"] != VERCEL_JEV_MODEL:
        raise ValueError(f"vercel-ai-gateway requires provider.model={VERCEL_JEV_MODEL}")
    return VercelGatewayAdapter(api_key, provider["base_url"])
