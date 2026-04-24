from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from agents.graph.nodes.dedupe import dedupe_node
from agents.graph.nodes.enrich import enrich_node
from agents.graph.nodes.fingerprint import fingerprint_node
from agents.graph.nodes.parse import parse_node
from agents.graph.nodes.publish import publish_node
from agents.graph.state import TriageState


def _build_graph():
    g = StateGraph(TriageState)
    g.add_node("parse", parse_node)
    g.add_node("fingerprint", fingerprint_node)
    g.add_node("dedupe", dedupe_node)
    g.add_node("enrich", enrich_node)
    g.add_node("publish", publish_node)

    g.add_edge(START, "parse")
    g.add_edge("parse", "fingerprint")
    g.add_edge("fingerprint", "dedupe")
    g.add_edge("dedupe", "enrich")
    g.add_edge("enrich", "publish")
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
    return graph.invoke(initial)
