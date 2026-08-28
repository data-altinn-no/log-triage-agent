from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from agents.graph.nodes.dedupe import dedupe_node
from agents.graph.nodes.enrich import enrich_node
from agents.graph.nodes.fingerprint import fingerprint_node
from agents.graph.nodes.fix import fix_node
from agents.graph.nodes.locate import locate_node
from agents.graph.nodes.parse import parse_node
from agents.graph.nodes.plan import plan_node
from agents.graph.nodes.publish import publish_node
from agents.graph.state import TriageState
from shared.config import get_settings
from shared.tracing import langchain_config


def _route_after_enrich(state: TriageState) -> str:
    """Decide whether to attempt an auto-fix branch after enrichment.

    We skip the fix branch entirely when:
    - auto-fix is disabled,
    - the issue is a duplicate of an existing public one,
    - the target repo is not configured.
    """
    settings = get_settings()
    if not settings.autofix_enabled:
        return "publish"
    if not settings.autofix_target_owner or not settings.autofix_target_repo:
        return "publish"
    result = state.get("result")
    if result is not None and result.is_duplicate:
        return "publish"
    return "locate"


def _build_graph():
    g = StateGraph(TriageState)
    g.add_node("parse", parse_node)
    g.add_node("fingerprint", fingerprint_node)
    g.add_node("dedupe", dedupe_node)
    g.add_node("enrich", enrich_node)
    g.add_node("locate", locate_node)
    g.add_node("plan", plan_node)
    g.add_node("fix", fix_node)
    g.add_node("publish", publish_node)

    g.add_edge(START, "parse")
    g.add_edge("parse", "fingerprint")
    g.add_edge("fingerprint", "dedupe")
    g.add_edge("dedupe", "enrich")
    g.add_conditional_edges(
        "enrich",
        _route_after_enrich,
        {"publish": "publish", "locate": "locate"},
    )
    g.add_edge("locate", "plan")
    g.add_edge("plan", "fix")
    g.add_edge("fix", "publish")
    g.add_edge("publish", END)
    return g.compile()


@lru_cache
def get_graph():
    return _build_graph()


def run_triage(*, issue_number: int, title: str, body: str, labels: list[str]) -> TriageState:
    graph = get_graph()
    initial: TriageState = {
        "issue_number": issue_number,
        "issue_title": title,
        "issue_body": body,
        "issue_labels": labels,
    }
    return graph.invoke(
        initial,
        config=langchain_config(issue_number=issue_number, issue_title=title),
    )
