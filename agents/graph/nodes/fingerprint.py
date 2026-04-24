from agents.graph.state import TriageState
from agents.services.fingerprint import compute_fingerprint
from shared.models import TriageResult


def fingerprint_node(state: TriageState) -> TriageState:
    payload = state["payload"]
    fp = compute_fingerprint(payload.exception_type, payload.stack_trace)
    result = state.get("result") or TriageResult(
        fingerprint=fp, suggested_title=state.get("issue_title", ""), summary=""
    )
    result.fingerprint = fp
    return {"result": result}
