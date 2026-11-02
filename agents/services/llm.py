"""Chat model factory.

Two providers supported:

- `azure_openai` — Azure-hosted OpenAI (gpt-4o family) via langchain-openai.
- `azure_ai_foundry` — Anthropic Claude models hosted on Azure AI Foundry. Foundry
  exposes Claude through the native Anthropic Messages API at
  `https://<resource>.services.ai.azure.com/anthropic`, so we use langchain-anthropic
  backed by the SDK's `AnthropicFoundry` client.

Both authenticate with Entra ID through `DefaultAzureCredential`: `az login` locally,
the Container App's managed identity in Azure.

Both return a `BaseChatModel` that supports `bind_tools()` for the agent loop.

Note on `temperature`: Claude 5 models reject a non-default `temperature` /
`top_p` / `top_k` with a 400, so the Foundry path ignores the argument. It is
still honoured on the Azure OpenAI path, where the parameter is supported.
"""

from __future__ import annotations

from functools import cached_property, lru_cache

from anthropic import AnthropicFoundry, AsyncAnthropicFoundry
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI

from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)

_FOUNDRY_SCOPE = "https://ai.azure.com/.default"
_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


def _token_provider(scope: str):
    # Called per request; reuses its token and fetches a new one shortly before expiry.
    return get_bearer_token_provider(_credential(), scope)


class _FoundryChatAnthropic(ChatAnthropic):
    """ChatAnthropic whose clients send an Entra token on every request instead of a key."""

    def _foundry_params(self) -> dict:
        params = {k: v for k, v in self._client_params.items() if k != "api_key"}
        return {**params, "azure_ad_token_provider": _token_provider(_FOUNDRY_SCOPE)}

    @cached_property
    def _client(self) -> AnthropicFoundry:
        return AnthropicFoundry(**self._foundry_params())

    @cached_property
    def _async_client(self) -> AsyncAnthropicFoundry:
        return AsyncAnthropicFoundry(**self._foundry_params())


@lru_cache
def get_chat_model(temperature: float = 0.1) -> BaseChatModel:
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()

    if provider == "azure_ai_foundry":
        if not settings.azure_ai_foundry_endpoint:
            raise RuntimeError("llm_provider=azure_ai_foundry requires AZURE_AI_FOUNDRY_ENDPOINT")
        if temperature is not None:
            log.debug("llm.temperature_ignored", provider=provider, requested=temperature)
        # Deliberately no `temperature`: Claude 5 rejects non-default sampling
        # parameters. Determinism comes from the prompt, not from temperature=0.
        return _FoundryChatAnthropic(
            model=settings.azure_ai_foundry_model,
            anthropic_api_url=settings.azure_ai_foundry_endpoint.rstrip("/"),
        )

    # Default: Azure OpenAI
    if not settings.azure_openai_endpoint:
        raise RuntimeError("llm_provider=azure_openai requires AZURE_OPENAI_ENDPOINT")
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=_token_provider(_OPENAI_SCOPE),
        api_version=settings.azure_openai_api_version,
        azure_deployment=settings.azure_openai_deployment,
        temperature=temperature,
    )
