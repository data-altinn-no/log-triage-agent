"""Thin wrapper around PyGithub supporting two repos:

- INPUT repo (private): Azure Function creates raw issues here; agent reads & closes.
- OUTPUT repo (public): agent creates polished, deduped issues here.
"""

from functools import lru_cache

from github import Auth, Github
from github.Issue import Issue
from github.Repository import Repository

from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)

FINGERPRINT_MARKER = "<!-- fingerprint:"


@lru_cache
def _client() -> Github:
    settings = get_settings()
    if not settings.github_token:
        raise RuntimeError("GITHUB_TOKEN is not configured")
    return Github(auth=Auth.Token(settings.github_token))


def input_repo() -> Repository:
    return _client().get_repo(get_settings().input_full_repo)


def output_repo() -> Repository:
    return _client().get_repo(get_settings().output_full_repo)


# ---------- input repo (private landing zone) ----------

def get_input_issue(number: int) -> Issue:
    return input_repo().get_issue(number=number)


def close_input_issue(number: int, *, link_to_public: str | None, reason: str) -> None:
    try:
        issue = get_input_issue(number)
    except Exception:
        log.warning("close_input_issue.not_found", number=number)
        return
    msg = reason
    if link_to_public:
        msg += f"\n\nPublished to: {link_to_public}"
    issue.create_comment(msg)
    issue.edit(state="closed", state_reason="completed")


# ---------- output repo (public published issues) ----------

def find_output_issue_by_fingerprint(fingerprint: str) -> Issue | None:
    """Search the OUTPUT repo for an open issue carrying this fingerprint marker."""
    settings = get_settings()
    query = (
        f"repo:{settings.output_full_repo} is:issue is:open "
        f'"{FINGERPRINT_MARKER} {fingerprint}"'
    )
    for issue in _client().search_issues(query=query):
        return issue
    return None


def create_output_issue(*, title: str, body: str, labels: list[str]) -> Issue:
    return output_repo().create_issue(title=title, body=body, labels=labels)


def comment_output_issue(number: int, body: str) -> None:
    output_repo().get_issue(number=number).create_comment(body)


# ---------- auto-fix target repo (code fixes via PR) ----------

def autofix_target_repo() -> Repository:
    return _client().get_repo(get_settings().autofix_target_full_repo)


def autofix_clone_url() -> str:
    """HTTPS clone URL with the agent's token baked in for push auth.

    Keep this server-side only; never log the result.
    """
    settings = get_settings()
    return (
        f"https://x-access-token:{settings.github_token}@github.com/"
        f"{settings.autofix_target_full_repo}.git"
    )


def open_autofix_pr(
    *,
    branch: str,
    title: str,
    body: str,
    labels: list[str],
    draft: bool = False,
):
    """Open a PR against autofix_target.autofix_base_branch. Returns PyGithub PR."""
    settings = get_settings()
    repo = autofix_target_repo()
    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch,
        base=settings.autofix_base_branch,
        draft=draft,
    )
    if labels:
        try:
            repo.get_issue(pr.number).add_to_labels(*labels)
        except Exception as exc:  # labels are best-effort
            log.warning("pr.label_failed", pr=pr.number, error=str(exc))
    return pr


# ---------- shared ----------

def render_fingerprint_marker(fingerprint: str) -> str:
    return f"{FINGERPRINT_MARKER} {fingerprint} -->"
