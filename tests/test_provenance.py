from types import SimpleNamespace

from agents.graph.nodes import provenance as node
from agents.graph.nodes.publish import _render_public_body
from agents.services.deployed_commit import CommitPick
from shared.models import CodeProvenance, ErrorPayload, TriageResult

STACK = (
    "   at Ns.MatrikkelStoreClientService+<GetVeg>d__7.MoveNext() in "
    "/home/runner/work/plugin-kartverket/plugin-kartverket/src/Svc.cs:line 143"
)


def _state(**overrides):
    state = {
        "payload": ErrorPayload(stack_trace=STACK, timestamp="2026-05-07T12:20:13Z"),
        "result": TriageResult(fingerprint="f", suggested_title="t", summary="s"),
        "output_repo": "data-altinn-no/plugin-kartverket",
    }
    state.update(overrides)
    return state


def _github(monkeypatch, head="headsha", fail=False):
    def get_repo(name):
        if fail:
            raise RuntimeError("api down")
        return SimpleNamespace(
            default_branch="main",
            get_branch=lambda b: SimpleNamespace(commit=SimpleNamespace(sha=head)),
        )
    monkeypatch.setattr(node.gh, "client", lambda: SimpleNamespace(get_repo=get_repo))


def test_records_the_deployed_commit_and_what_changed_since(monkeypatch):
    _github(monkeypatch)
    monkeypatch.setattr(node.deployed_commit, "resolve",
                        lambda *a, **k: CommitPick("oldsha", True, "line 143 is in GetVeg"))
    monkeypatch.setattr(node.deployed_commit, "changes_since",
                        lambda *a, **k: ["8e6719d Refactor Kartverket WS clients (#87)"])

    p = node.provenance_node(_state())["provenance"]

    assert (p.repo, p.file_path, p.line, p.deployed_sha) == (
        "data-altinn-no/plugin-kartverket", "src/Svc.cs", 143, "oldsha")
    assert p.changed_on_branch == ["8e6719d Refactor Kartverket WS clients (#87)"]


def test_no_history_check_when_the_deployed_commit_is_the_branch_head(monkeypatch):
    _github(monkeypatch, head="samesha")
    monkeypatch.setattr(node.deployed_commit, "resolve",
                        lambda *a, **k: CommitPick("samesha", True, "ok"))
    monkeypatch.setattr(node.deployed_commit, "changes_since",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))

    assert node.provenance_node(_state())["provenance"].changed_on_branch == []


def test_a_github_failure_never_blocks_publishing(monkeypatch):
    _github(monkeypatch, fail=True)
    assert node.provenance_node(_state()) == {}


def test_duplicates_and_frameless_errors_are_skipped(monkeypatch):
    _github(monkeypatch)
    duplicate = TriageResult(fingerprint="f", suggested_title="t", summary="s", is_duplicate=True)
    assert node.provenance_node(_state(result=duplicate)) == {}
    assert node.provenance_node(_state(payload=ErrorPayload(stack_trace="no frames"))) == {}


def _published(provenance=None):
    state = _state()
    if provenance:
        state["provenance"] = provenance
    return _render_public_body(state)


def test_published_issue_lists_commits_when_the_method_has_moved():
    body = _published(CodeProvenance(
        repo="r", branch="main", file_path="src/Svc.cs", line=143,
        changed_on_branch=["cfd8bce Fix duplicate key crash"],
    ))
    assert "## Code history" in body and "- cfd8bce Fix duplicate key crash" in body


def test_published_issue_has_no_history_section_when_nothing_moved():
    assert "Code history" not in _published(CodeProvenance(
        repo="r", branch="main", file_path="src/Svc.cs", line=143))
    assert "Code history" not in _published()
