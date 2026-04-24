import json

from langchain_core.messages import HumanMessage, SystemMessage

from agents.graph.state import TriageState
from agents.prompts.enrich import SYSTEM_PROMPT, USER_TEMPLATE
from agents.services.llm import get_chat_model
from shared.logging import get_logger

log = get_logger(__name__)


def enrich_node(state: TriageState) -> TriageState:
    result = state["result"]
    if result.is_duplicate:
        # Skip enrichment for duplicates; we're going to close them anyway.
        return {"result": result}

    payload = state["payload"]
    user_msg = USER_TEMPLATE.format(
        exception_type=payload.exception_type or "unknown",
        message=payload.message or "",
        cloud_role=payload.cloud_role or "",
        operation=payload.operation or "",
        request_path=payload.request_path or "",
        timestamp=payload.timestamp or "",
        correlation_id=payload.correlation_id or "",
        stack_trace=(payload.stack_trace or "")[:4000],
    )

    llm = get_chat_model()
    response = llm.invoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_msg)]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)

    try:
        # Strip ```json fences if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]
        data = json.loads(cleaned)
    except (json.JSONDecodeError, IndexError) as exc:
        log.warning("enrich.parse_failed", error=str(exc), raw=content[:500])
        data = {}

    result.category = data.get("category", "unknown")
    result.severity = data.get("severity", "medium")
    result.suggested_title = data.get("suggested_title") or result.suggested_title
    result.summary = data.get("summary", "")
    result.root_cause_hypothesis = data.get("root_cause_hypothesis", "")
    result.suggested_owner = data.get("suggested_owner")
    result.labels = data.get("labels", []) or []
    return {"result": result}
