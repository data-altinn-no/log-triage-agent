"""Chat model factory.

Two providers supported:

- `azure_openai` — Azure-hosted OpenAI (gpt-4o family) via langchain-openai.
- `azure_ai_foundry` — Anthropic Claude models hosted on Azure AI Foundry. Foundry
  exposes Claude through the native Anthropic Messages API at
  `https://<resource>.services.ai.azure.com/anthropic`, so we use langchain-anthropic
  with `anthropic_api_url` overridden to the Foundry endpoint.

Both return a `BaseChatModel` that supports `bind_tools()` for the agent loop.

Note on `temperature`: Claude 5 models reject a non-default `temperature` /
`top_p` / `top_k` with a 400, so the Foundry path ignores the argument. It is
still honoured on the Azure OpenAI path, where the parameter is supported.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI

from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)


@lru_cache
def get_chat_model(temperature: float = 0.1) -> BaseChatModel:
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()

    if provider == "azure_ai_foundry":
        if not settings.azure_ai_foundry_endpoint or not settings.azure_ai_foundry_api_key:
            raise RuntimeError(
                "llm_provider=azure_ai_foundry requires AZURE_AI_FOUNDRY_ENDPOINT and "
                "AZURE_AI_FOUNDRY_API_KEY"
            )
        if temperature is not None:
            log.debug("llm.temperature_ignored", provider=provider, requested=temperature)
        # Deliberately no `temperature`: Claude 5 rejects non-default sampling
        # parameters. Determinism comes from the prompt, not from temperature=0.
        return ChatAnthropic(
            model=settings.azure_ai_foundry_model,
            anthropic_api_url=settings.azure_ai_foundry_endpoint.rstrip("/"),
            anthropic_api_key=settings.azure_ai_foundry_api_key,
        )

    # Default: Azure OpenAI
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        raise RuntimeError(
            "llm_provider=azure_openai requires AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT"
        )
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_deployment=settings.azure_openai_deployment,
        temperature=temperature,
    )
