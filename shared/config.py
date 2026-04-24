from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Azure OpenAI
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment: str = "gpt-4o"

    # GitHub — two repos:
    #   input: private triage repo (Azure Function posts raw issues here)
    #   output: public repo where the agent publishes polished, deduped issues
    github_token: str = ""
    github_input_owner: str = "data-altinn-no"
    github_input_repo: str = "core-triage"
    github_output_owner: str = "data-altinn-no"
    github_output_repo: str = "core"
    github_webhook_secret: str = ""
    triage_label: str = "auto-triage"
    published_label: str = "auto-published"

    # Langfuse
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Server
    host: str = "0.0.0.0"
    port: int = 8081
    log_level: str = "INFO"

    @property
    def input_full_repo(self) -> str:
        return f"{self.github_input_owner}/{self.github_input_repo}"

    @property
    def output_full_repo(self) -> str:
        return f"{self.github_output_owner}/{self.github_output_repo}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
