from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from anthropic import AnthropicFoundry, AsyncAnthropicFoundry
from langchain_openai import AzureChatOpenAI

from agents.services import llm
from shared.config import Settings

_ENDPOINT = "https://team-dataaltinnno-ai.services.ai.azure.com/anthropic"


@pytest.fixture
def credential(monkeypatch):
    cred = MagicMock()
    # Already expired, so the provider has to ask again on every request.
    cred.get_token_info.side_effect = lambda scope, **_: SimpleNamespace(
        token=f"token-for-{scope}", expires_on=0
    )
    monkeypatch.setattr(llm, "DefaultAzureCredential", lambda: cred)
    for var in ("ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    llm._credential.cache_clear()
    llm.get_chat_model.cache_clear()
    yield cred
    llm._credential.cache_clear()
    llm.get_chat_model.cache_clear()


def _use(monkeypatch, **settings):
    monkeypatch.setattr(llm, "get_settings", lambda: Settings(_env_file=None, **settings))


def test_foundry_model_sends_an_entra_token_and_refreshes_it_when_expired(monkeypatch, credential):
    _use(monkeypatch, llm_provider="azure_ai_foundry", azure_ai_foundry_endpoint=_ENDPOINT)

    model = llm.get_chat_model()

    assert isinstance(model._client, AnthropicFoundry)
    assert isinstance(model._async_client, AsyncAnthropicFoundry)
    assert str(model._client.base_url).rstrip("/") == _ENDPOINT
    assert model._client._get_azure_ad_token() == "token-for-https://ai.azure.com/.default"
    model._client._get_azure_ad_token()
    assert credential.get_token_info.call_count == 2


def test_azure_openai_model_uses_the_cognitive_services_scope(monkeypatch, credential):
    _use(
        monkeypatch,
        llm_provider="azure_openai",
        azure_openai_endpoint="https://example.openai.azure.com/",
    )

    model = llm.get_chat_model()

    assert isinstance(model, AzureChatOpenAI)
    assert (
        model.azure_ad_token_provider()
        == "token-for-https://cognitiveservices.azure.com/.default"
    )


@pytest.mark.parametrize(
    ("provider", "missing"),
    [("azure_ai_foundry", "AZURE_AI_FOUNDRY_ENDPOINT"), ("azure_openai", "AZURE_OPENAI_ENDPOINT")],
)
def test_missing_endpoint_is_named_in_the_error(monkeypatch, credential, provider, missing):
    _use(monkeypatch, llm_provider=provider, azure_openai_endpoint="")

    with pytest.raises(RuntimeError, match=missing):
        llm.get_chat_model()
