from datetime import UTC, datetime, timedelta

from agents.graph.state import TriageState
from agents.services import github as gh
from agents.services.router import resolve_repo
from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)


def dedupe_node(state: TriageState) -> TriageState:
    """Check the OUTPUT (public) repo for an issue with the same fingerprint.

    An open one is a duplicate; a closed one is a regression if it recurred after a fix.
    """
    result = state["result"]
    route = resolve_repo(state["payload"])
    existing = gh.find_output_issue_by_fingerprint(result.fingerprint, route.full_repo)
    if existing is None:
        existing = gh.find_output_issue_by_fingerprint(
            result.fingerprint, route.full_repo, state="closed"
        )
        if existing is not None:
            result.duplicate_closed = True
            result.is_regression = _recurred_after_fix(existing, state["payload"].timestamp)
    if existing:
        result.is_duplicate = True
        result.duplicate_of = existing.number
        log.info(
            "dedupe.match",
            fingerprint=result.fingerprint,
            duplicate_of_public=existing.number,
            closed=result.duplicate_closed,
            regression=result.is_regression,
            private_number=state.get("issue_number"),
        )
    log.info("dedupe.routed", repo=route.full_repo, source=route.source)
    return {"result": result, "output_repo": route.full_repo}


def _recurred_after_fix(issue, occurred_at: str | None) -> bool:
    # "not_planned" means the team chose to live with it; only a completed issue counts as fixed.
    if issue.state_reason != "completed":
        return False
    if not occurred_at or issue.closed_at is None:
        return True
    try:
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    grace = timedelta(minutes=get_settings().regression_grace_minutes)
    return occurred > issue.closed_at + grace
