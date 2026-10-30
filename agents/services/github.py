"""Thin wrapper around PyGithub supporting two repos:

- INPUT repo (private): Azure Function creates raw issues here; agent reads & closes.
- OUTPUT repo (public): agent creates polished, deduped issues here.
"""

import base64
import binascii
from functools import lru_cache

from github import Auth, Github
from github.Issue import Issue
from github.Repository import Repository

from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)

FINGERPRINT_MARKER = "<!-- fingerprint:"


def read_private_key(configured: str) -> str:
    value = configured.strip()
    if "-----BEGIN" in value:
        return value
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "GITHUB_APP_PRIVATE_KEY is neither PEM nor base64-encoded PEM"
        ) from exc


@lru_cache
def _auth() -> Auth.Auth:
    settings = get_settings()
    if settings.uses_github_app:
        app = Auth.AppAuth(
            settings.github_app_client_id, read_private_key(settings.github_app_private_key)
        )
        return Auth.AppInstallationAuth(app, settings.github_app_installation_id)
    if not settings.github_token:
        raise RuntimeError(
            "no GitHub credentials: set GITHUB_APP_CLIENT_ID, GITHUB_APP_INSTALLATION_ID "
            "and GITHUB_APP_PRIVATE_KEY, or GITHUB_TOKEN"
        )
    return Auth.Token(settings.github_token)


@lru_cache
def client() -> Github:
    return Github(auth=_auth())


def access_token() -> str:
    # Reading .token before the client exists raises: PyGithub binds the
    # requester the installation auth needs when Github() is constructed.
    client()
    return _auth().token


def input_repo() -> Repository:
    return client().get_repo(get_settings().input_full_repo)


def output_repo(full_repo: str | None = None) -> Repository:
    return client().get_repo(full_repo or get_settings().output_full_repo)


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


def hold_input_issue(number: int, *, label: str, comment: str) -> None:
    issue = get_input_issue(number)
    issue.create_comment(comment)
    issue.add_to_labels(label)


# ---------- output repo (public published issues) ----------

def find_output_issue_by_fingerprint(
    fingerprint: str, full_repo: str | None = None
) -> Issue | None:
    """Search the OUTPUT repo for an open issue carrying this fingerprint marker."""
    repo = full_repo or get_settings().output_full_repo
    query = (
        f"repo:{repo} is:issue is:open "
        f'"{FINGERPRINT_MARKER} {fingerprint}"'
    )
    for issue in client().search_issues(query=query):
        return issue
    return None


def create_output_issue(
    *, title: str, body: str, labels: list[str], full_repo: str | None = None
) -> Issue:
    return output_repo(full_repo).create_issue(title=title, body=body, labels=labels)


def comment_output_issue(number: int, body: str, full_repo: str | None = None) -> None:
    output_repo(full_repo).get_issue(number=number).create_comment(body)


# ---------- auto-fix (code fixes via draft PR) ----------

def default_branch(full_repo: str) -> str:
    return client().get_repo(full_repo).default_branch


def autofix_clone_url(full_repo: str) -> str:
    """HTTPS clone URL with the agent's token baked in for push auth. Never log it."""
    return f"https://x-access-token:{access_token()}@github.com/{full_repo}.git"


def dispatch_verify(*, full_repo: str, branch: str, request_id: str) -> None:
    """Ask the input repo's verify workflow to build and test `branch` of `full_repo`."""
    settings = get_settings()
    input_repo().create_repository_dispatch(
        settings.autofix_verify_event,
        {"repo": full_repo, "ref": branch, "request_id": request_id},
    )


def find_verify_run(request_id: str):
    """The verify run whose title carries `request_id`, or None until it has started."""
    workflow = input_repo().get_workflow(get_settings().autofix_verify_workflow)
    for run in workflow.get_runs(event="repository_dispatch").get_page(0):
        if run.display_title == f"verify {request_id}":
            return run
    return None


def delete_branch(full_repo: str, branch: str) -> None:
    client().get_repo(full_repo).get_git_ref(f"heads/{branch}").delete()


def open_autofix_pr(
    *,
    full_repo: str,
    base: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str],
):
    """Open a draft PR from `branch` into `base`. Returns the PyGithub PR."""
    repo = client().get_repo(full_repo)
    pr = repo.create_pull(title=title, body=body, head=branch, base=base, draft=True)
    if labels:
        try:
            repo.get_issue(pr.number).add_to_labels(*labels)
        except Exception as exc:  # labels are best-effort
            log.warning("pr.label_failed", pr=pr.number, error=str(exc))
    return pr


# ---------- shared ----------

def render_fingerprint_marker(fingerprint: str) -> str:
    return f"{FINGERPRINT_MARKER} {fingerprint} -->"
