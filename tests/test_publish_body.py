import pytest
from github import GithubException

from agents.graph.nodes import publish
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



class _GitHub:
    def __init__(self, status):
        self.status, self.held, self.closed = status, [], []

    def create_output_issue(self, **kwargs):
        raise GithubException(self.status, {"message": "Not Found"}, None)

    def hold_input_issue(self, number, *, label, comment):
        self.held.append((number, label, comment))

    def close_input_issue(self, number, **kwargs):
        self.closed.append(number)

    render_fingerprint_marker = staticmethod(lambda fp: f"<!-- fingerprint: {fp} -->")


def _publish(monkeypatch, status):
    fake = _GitHub(status)
    monkeypatch.setattr(publish, "gh", fake)
    state = {
        "issue_number": 42,
        "payload": ErrorPayload(),
        "result": TriageResult(fingerprint="f", suggested_title="t", summary="s"),
        "output_repo": "data-altinn-no/plugin-x",
    }
    publish.publish_node(state)
    return fake


@pytest.mark.parametrize("status", [403, 404, 410])
def test_a_repo_that_refuses_the_issue_keeps_it_open_in_triage(monkeypatch, status):
    fake = _publish(monkeypatch, status)
    assert fake.closed == []
    number, label, comment = fake.held[0]
    assert (number, label) == (42, "publish-failed")
    assert "plugin-x" in comment and "## Summary" in comment


def test_other_github_errors_still_fail_loudly(monkeypatch):
    with pytest.raises(GithubException):
        _publish(monkeypatch, 500)


def test_published_issues_carry_only_the_fixed_labels(monkeypatch):
    created = {}

    class _Publishing(_GitHub):
        def create_output_issue(self, **kwargs):
            created.update(kwargs)
            return type("Issue", (), {"number": 7, "html_url": "u"})()

    monkeypatch.setattr(publish, "gh", _Publishing(None))
    publish.publish_node({
        "issue_number": 42,
        "payload": ErrorPayload(),
        "result": TriageResult(
            fingerprint="f", suggested_title="t", summary="s", severity="high", category="timeout"
        ),
        "output_repo": "data-altinn-no/plugin-x",
    })

    assert created["labels"] == ["auto-published", "category:timeout", "severity:high"]
