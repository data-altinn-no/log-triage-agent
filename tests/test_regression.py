from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agents.graph.nodes import dedupe, publish
from agents.services.router import RepoRoute
from shared.models import AutoFixOutcome, ErrorPayload, TriageResult

_CLOSED_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _issue(number, *, state_reason=None, closed_at=None):
    return SimpleNamespace(number=number, state_reason=state_reason, closed_at=closed_at)


def _dedupe(monkeypatch, *, open_issue=None, closed_issue=None, occurred="2026-11-02T08:00:00Z"):
    def find(fingerprint, full_repo, *, state="open"):
        return open_issue if state == "open" else closed_issue

    monkeypatch.setattr(dedupe.gh, "find_output_issue_by_fingerprint", find)
    monkeypatch.setattr(
        dedupe, "resolve_repo", lambda p: RepoRoute("data-altinn-no/plugin-x", "test")
    )
    state = {
        "payload": ErrorPayload(timestamp=occurred),
        "result": TriageResult(fingerprint="f", suggested_title="t", summary="s"),
    }
    return dedupe.dedupe_node(state)["result"]


def test_an_open_issue_is_a_plain_duplicate(monkeypatch):
    result = _dedupe(monkeypatch, open_issue=_issue(7))
    assert result.duplicate_of == 7 and not result.duplicate_closed and not result.is_regression


def test_recurring_after_a_completed_close_is_a_regression(monkeypatch):
    closed = _issue(7, state_reason="completed", closed_at=_CLOSED_AT)
    result = _dedupe(monkeypatch, closed_issue=closed)
    assert result.is_duplicate and result.duplicate_of == 7 and result.is_regression


@pytest.mark.parametrize("occurred", ["2026-11-01T11:59:00Z", "2026-11-01T12:30:00Z"])
def test_an_occurrence_before_the_fix_could_deploy_is_not_a_regression(monkeypatch, occurred):
    closed = _issue(7, state_reason="completed", closed_at=_CLOSED_AT)
    result = _dedupe(monkeypatch, closed_issue=closed, occurred=occurred)
    assert result.duplicate_closed and not result.is_regression


def test_an_issue_closed_as_not_planned_stays_closed(monkeypatch):
    closed = _issue(7, state_reason="not_planned", closed_at=_CLOSED_AT)
    result = _dedupe(monkeypatch, closed_issue=closed)
    assert result.duplicate_closed and not result.is_regression


def test_no_match_at_all_is_a_new_issue(monkeypatch):
    result = _dedupe(monkeypatch)
    assert not result.is_duplicate and result.duplicate_of is None


class _GitHub:
    render_fingerprint_marker = staticmethod(lambda fp: f"<!-- fingerprint: {fp} -->")

    def __init__(self):
        self.comments, self.reopened, self.linked, self.closed = [], [], [], []

    def comment_output_issue(self, number, body, full_repo):
        self.comments.append((number, body))

    def reopen_output_issue(self, number, *, label, full_repo):
        self.reopened.append((number, label))

    def create_output_issue(self, **kwargs):
        return SimpleNamespace(number=87, html_url="https://github.com/x/issues/87")

    def link_pr_to_issue(self, *, full_repo, pr_number, issue_number):
        self.linked.append((full_repo, pr_number, issue_number))

    def close_input_issue(self, number, **kwargs):
        self.closed.append((number, kwargs["reason"]))


def _publish(monkeypatch, result, autofix=None):
    fake = _GitHub()
    monkeypatch.setattr(publish, "gh", fake)
    publish.publish_node({
        "issue_number": 42,
        "payload": ErrorPayload(timestamp="2026-11-02T08:00:00Z"),
        "result": result,
        "autofix": autofix,
        "output_repo": "data-altinn-no/plugin-x",
    })
    return fake


def _result(**kwargs):
    return TriageResult(fingerprint="f", suggested_title="t", summary="s", **kwargs)


def test_a_regression_reopens_the_public_issue_with_a_label(monkeypatch):
    fake = _publish(
        monkeypatch,
        _result(is_duplicate=True, duplicate_of=7, duplicate_closed=True, is_regression=True),
    )
    assert fake.reopened == [(7, "regression")]
    assert "Regression" in fake.comments[0][1] and "2026-11-02T08:00:00Z" in fake.comments[0][1]
    assert fake.closed[0][1].startswith("Regression")


def test_an_occurrence_on_a_closed_issue_only_comments(monkeypatch):
    fake = _publish(monkeypatch, _result(is_duplicate=True, duplicate_of=7, duplicate_closed=True))
    assert fake.reopened == []
    assert "while this issue is closed" in fake.comments[0][1]


@pytest.mark.parametrize(
    ("pr_number", "linked"), [(86, [("data-altinn-no/plugin-x", 86, 87)]), (None, [])]
)
def test_a_new_issue_links_the_fix_pr_so_merging_closes_it(monkeypatch, pr_number, linked):
    fake = _publish(monkeypatch, _result(), AutoFixOutcome(attempted=True, pr_number=pr_number))
    assert fake.linked == linked
