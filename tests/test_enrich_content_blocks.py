from unittest.mock import patch

from langchain_core.messages import AIMessage

from agents.graph.nodes.enrich import enrich_node
from shared.models import ErrorPayload, TriageResult


def _run(content):
    class _Model:
        def invoke(self, _messages):
            return AIMessage(content=content)

    state = {
        "payload": ErrorPayload(exception_type="E"),
        "result": TriageResult(fingerprint="f", suggested_title="t", summary="s"),
    }
    with patch("agents.graph.nodes.enrich.get_chat_model", return_value=_Model()):
        return enrich_node(state)["result"]


def test_parses_json_when_the_reply_carries_a_thinking_block():
    result = _run([
        {"type": "thinking", "thinking": "reasoning", "signature": "sig"},
        {"type": "text", "text": '{"severity": "high", "category": "data-integrity"}'},
    ])
    assert (result.severity, result.category) == ("high", "data-integrity")


def test_parses_plain_string_replies():
    result = _run('{"severity": "low", "category": "timeout"}')
    assert (result.severity, result.category) == ("low", "timeout")
