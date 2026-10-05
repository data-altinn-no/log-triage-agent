from agents.graph.nodes.publish import _render_public_body
from shared.models import AutoFixOutcome, ErrorPayload, TriageResult


def _body(autofix):
    return _render_public_body({
        "payload": ErrorPayload(),
        "result": TriageResult(fingerprint="f", suggested_title="t", summary="s"),
        "autofix": autofix,
    })


def test_a_declined_fix_publishes_the_agents_reasoning():
    body = _body(AutoFixOutcome(
        attempted=True,
        skipped_reason="agent declined to patch",
        declined_reason="Caught on purpose\nand rethrown as a domain error.",
    ))
    assert "## Agent assessment" in body
    assert "> Caught on purpose and rethrown as a domain error." in body


def test_a_plain_skip_still_reads_as_skipped():
    body = _body(AutoFixOutcome(attempted=True, skipped_reason="no parseable frame"))
    assert "Agent assessment" not in body and "no parseable frame" in body
