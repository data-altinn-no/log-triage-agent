"""Azure OpenAI client factory."""

from functools import lru_cache

from langchain_openai import AzureChatOpenAI

from shared.config import get_settings


@lru_cache
def get_chat_model(temperature: float = 0.1) -> AzureChatOpenAI:
    settings = get_settings()
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_deployment=settings.azure_openai_deployment,
        temperature=temperature,
    )
