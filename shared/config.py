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

    # Auto-fix pipeline (the core workflow: error → triage issue → PR).
    # target_* is the repo the agent proposes code fixes against; kept separate
    # from the output repo so fixes can land in a *code* repo while triage
    # issues continue to land in an issue-only repo.
    # `autofix_enabled` is a runtime kill-switch: flip to False to suspend PR
    # creation during incidents without redeploying.
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
