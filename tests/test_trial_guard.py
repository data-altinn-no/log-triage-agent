import importlib.util
from pathlib import Path

import pytest
from github.Requester import Requester

from agents.services.workspace import Workspace

_spec = importlib.util.spec_from_file_location(
    "trial", Path(__file__).resolve().parents[1] / "scripts" / "trial.py"
)
trial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trial)


@pytest.fixture
def guarded(monkeypatch):
    for name in ("requestJson", "requestMultipart", "requestBlob"):
        monkeypatch.setattr(Requester, name, getattr(Requester, name))
    monkeypatch.setattr(Workspace, "push_branch", Workspace.push_branch)
    trial._refuse_writes()


@pytest.mark.parametrize("verb", ["POST", "PATCH", "PUT", "DELETE"])
def test_every_github_write_is_refused(guarded, verb):
    with pytest.raises(RuntimeError, match="trial refused a GitHub write"):
        Requester.requestJson(None, verb, "https://api.github.com/repos/o/r/issues")


def test_git_push_is_refused(guarded):
    with pytest.raises(RuntimeError, match="trial refused a git push"):
        Workspace.push_branch(None, "auto-fix/x", "https://example")
