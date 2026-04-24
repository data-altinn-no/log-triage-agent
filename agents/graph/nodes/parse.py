from agents.graph.state import TriageState
from agents.services.parser import parse_issue_body


def parse_node(state: TriageState) -> TriageState:
    payload = parse_issue_body(state.get("issue_body", ""))
    return {"payload": payload}
