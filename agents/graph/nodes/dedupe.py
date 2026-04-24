from agents.graph.state import TriageState
from agents.services import github as gh
from shared.logging import get_logger

log = get_logger(__name__)


def dedupe_node(state: TriageState) -> TriageState:
    """Check the OUTPUT (public) repo for an open issue with the same fingerprint."""
    result = state["result"]
    existing = gh.find_output_issue_by_fingerprint(result.fingerprint)
    if existing:
        result.is_duplicate = True
        result.duplicate_of = existing.number
        log.info(
            "dedupe.match",
            fingerprint=result.fingerprint,
            duplicate_of_public=existing.number,
            private_number=state.get("issue_number"),
        )
    return {"result": result}
