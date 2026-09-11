"""Decide which repo an error belongs to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from shared.config import get_settings
from shared.logging import get_logger
from shared.models import ErrorPayload

log = get_logger(__name__)

# Actions checks out at /work/<repo>/<repo>/, so the frame names its own repo.
_WORK_ROOT_RE = re.compile(r"/work/(?P<repo>[^/\s]+)/(?P=repo)/")

_CONVENTION_RE = re.compile(r"^func-es(?P<name>.+?)-prod-prod$")

# Only roles the convention gets wrong; conforming functions need no entry.
_ROLE_EXCEPTIONS = {
    "func-dancore-prod": "core",
    "func-danproxy-prod": "proxy",
    "func-esdig-prod-prod": "plugin-digdir",
    "func-espatent-prod-prod": "plugin-patentstyret",
    "func-esskatt-prod-prod": "plugin-skatteetaten",
    "func-essvv-prod-prod": "plugin-statensvegvesen",
}


@dataclass(frozen=True)
class RepoRoute:
    full_repo: str
    source: str

    @property
    def is_fallback(self) -> bool:
        return self.source == "default"


def resolve_repo(payload: ErrorPayload) -> RepoRoute:
    settings = get_settings()
    owner = settings.github_output_owner

    if repo := _repo_from_stack(payload.stack_trace):
        return RepoRoute(f"{owner}/{repo}", "stack")

    role = _strip_slot((payload.cloud_role or "").strip())

    if repo := settings.repo_route_map.get(role):
        return RepoRoute(f"{owner}/{repo}", "config")

    if repo := _ROLE_EXCEPTIONS.get(role):
        return RepoRoute(f"{owner}/{repo}", "exception")

    if (m := _CONVENTION_RE.match(role)) and (repo := f"plugin-{m.group('name')}"):
        if repo in org_repos(owner):
            return RepoRoute(f"{owner}/{repo}", "convention")
        log.info("router.convention_repo_missing", role=role, candidate=repo)

    return RepoRoute(settings.output_full_repo, "default")


@lru_cache
def org_repos(owner: str) -> frozenset[str]:
    """Repo names in the org, used to reject a convention guess that does not exist."""
    from agents.services import github as gh

    try:
        return frozenset(r.name for r in gh.client().get_organization(owner).get_repos())
    except Exception as exc:  # noqa: BLE001
        log.warning("router.org_repos_failed", owner=owner, error=str(exc))
        return frozenset()


def _repo_from_stack(stack_trace: str | None) -> str | None:
    if not stack_trace:
        return None
    for match in _WORK_ROOT_RE.finditer(stack_trace.replace("\\", "/")):
        return match.group("repo")
    return None


def _strip_slot(role: str) -> str:
    return role[: -len("-staging")] if role.endswith("-staging") else role
