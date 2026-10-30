import pytest

from agents.graph.nodes import fix
from shared.models import AutoFixOutcome, ErrorPayload, ProposedPatch, TriageResult


class _Workspace:
    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def clone(self, url, *, base_branch, depth):
        self.base_branch = base_branch

    def checkout_sha(self, sha):
        pass

    def configure_identity(self, name, email):
        pass

    def create_branch(self, name):
        pass

    def apply_diff(self, diff):
        pass

    def commit(self, message):
        return "abc1234567"

    def push_branch(self, name, url):
        pass


class _Run:
    def __init__(self, conclusion):
        self.status, self.conclusion = "completed", conclusion
        self.html_url = "https://github.com/data-altinn-no/log-triage/actions/runs/1"

    def update(self):
        pass


class _GitHub:
    def __init__(self, run):
        self.run, self.dispatched, self.deleted, self.prs = run, [], [], []

    def autofix_clone_url(self, repo):
        return f"https://x/{repo}.git"

    def dispatch_verify(self, **kwargs):
        self.dispatched.append(kwargs)

    def find_verify_run(self, request_id):
        return self.run

    def delete_branch(self, repo, branch):
        self.deleted.append((repo, branch))

    def open_autofix_pr(self, **kwargs):
        self.prs.append(kwargs)
        return type("PR", (), {"number": 5, "html_url": "https://github.com/pr/5"})()


@pytest.fixture
def state():
    return {
        "issue_number": 42,
        "payload": ErrorPayload(),
        "result": TriageResult(fingerprint="fp", suggested_title="t", summary="s"),
        "output_repo": "data-altinn-no/plugin-x",
        "autofix_base_branch": "main",
        "autofix": AutoFixOutcome(
            attempted=True,
            patch=ProposedPatch(diff="d", rationale="r", base_sha="abc"),
        ),
    }


def _run_fix(monkeypatch, state, run):
    fake = _GitHub(run)
    monkeypatch.setattr(fix, "gh", fake)
    monkeypatch.setattr(fix, "Workspace", _Workspace)
    monkeypatch.setattr(fix.time, "sleep", lambda _: None)
    return fake, fix.fix_node(state)["autofix"]


def test_a_fix_that_passes_ci_opens_a_draft_pr_into_the_routed_repo(monkeypatch, state):
    fake, outcome = _run_fix(monkeypatch, state, _Run("success"))

    assert fake.dispatched[0]["full_repo"] == "data-altinn-no/plugin-x"
    assert fake.dispatched[0]["branch"] == "auto-fix/fp-42"
    pr = fake.prs[0]
    assert (pr["full_repo"], pr["base"], pr["branch"]) == (
        "data-altinn-no/plugin-x", "main", "auto-fix/fp-42"
    )
    assert "actions/runs/1" in pr["body"]
    assert outcome.pr_url == "https://github.com/pr/5" and not outcome.skipped_reason


def test_a_fix_that_fails_ci_opens_no_pr_and_removes_its_branch(monkeypatch, state):
    fake, outcome = _run_fix(monkeypatch, state, _Run("failure"))

    assert fake.prs == []
    assert fake.deleted == [("data-altinn-no/plugin-x", "auto-fix/fp-42")]
    assert "failure" in outcome.skipped_reason and "actions/runs/1" in outcome.skipped_reason


def test_a_ci_run_that_never_starts_opens_no_pr(monkeypatch, state):
    monkeypatch.setattr(fix, "_POLL_S", 0)
    monkeypatch.setattr(fix, "get_settings", lambda: _settings(timeout_s=0))
    fake, outcome = _run_fix(monkeypatch, state, None)

    assert fake.prs == [] and fake.deleted
    assert "never started" in outcome.skipped_reason


def _settings(timeout_s):
    from shared.config import Settings

    return Settings(autofix_verify_timeout_s=timeout_s)
