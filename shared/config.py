from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "azure_openai" or "azure_ai_foundry" (Claude via the Messages API).
    llm_provider: str = "azure_openai"

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment: str = "gpt-4o"

    # Endpoint must end in /anthropic - that suffix routes to the Messages API.
    azure_ai_foundry_endpoint: str = ""
    azure_ai_foundry_api_key: str = ""
    azure_ai_foundry_model: str = "claude-sonnet-5"

    # input: private triage repo the monitor writes to.
    # output: public repo the agent publishes deduped issues to.
    github_token: str = ""
    github_input_owner: str = "data-altinn-no"
    github_input_repo: str = "log-triage"
    github_output_owner: str = "data-altinn-no"
    github_output_repo: str = "core"
    github_webhook_secret: str = ""
    # Comma-separated: the monitor's two pollers label separately, and a
    # single value here silently skips everything the other poller files.
    triage_label: str = "auto-triage-errors,auto-triage-exceptions,auto-triage"
    published_label: str = "auto-published"

    # Also accepts LANGFUSE_BASE_URL; extra="ignore" would silently drop it.
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    host: str = "0.0.0.0"
    port: int = 8081
    log_level: str = "INFO"
    # Only this flag lets an unsigned webhook through - never a missing secret.
    dev_mode: bool = False

    # autofix_target_* is the code repo fixes land in; separate from the
    # issue-only output repo. autofix_enabled is a runtime kill-switch.
    autofix_enabled: bool = False
    autofix_target_owner: str = ""
    autofix_target_repo: str = ""
    autofix_base_branch: str = "main"
    autofix_test_cmd: str = "pytest -q"
    autofix_lint_cmd: str = ""  # optional, e.g. "ruff check ."
    autofix_test_timeout_s: int = 300
    autofix_max_retries: int = 1
    autofix_max_diff_lines: int = 200
    autofix_workdir: str = "/tmp/log-triage-agent-ws"
    autofix_min_confidence: float = 0.5
    autofix_block_label: str = "no-auto-fix"
    autofix_git_user_name: str = "log-triage-agent[bot]"
    autofix_git_user_email: str = "log-triage-agent@users.noreply.github.com"

    @property
    def triage_labels(self) -> set[str]:
        """`triage_label` parsed into a set. Accepts a single value too."""
        return {p.strip() for p in self.triage_label.split(",") if p.strip()}

    @property
    def input_full_repo(self) -> str:
        return f"{self.github_input_owner}/{self.github_input_repo}"

    @property
    def output_full_repo(self) -> str:
        return f"{self.github_output_owner}/{self.github_output_repo}"

    @property
    def autofix_target_full_repo(self) -> str:
        return f"{self.autofix_target_owner}/{self.autofix_target_repo}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
