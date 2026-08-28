"""Loop-safety tests for the fix agent, driven by a fake chat model.

The two behaviours here are what stop a bad run from being worthless: a model
that repeats one failing call burns every turn and produces nothing, and a run
that hits the turn cap discards its edits entirely.
"""

from pathlib import Path

import pytest

from agents.services import agent_fix
from agents.services.workspace import Workspace
from shared.models import ErrorPayload, SuspectSite


class _Resp:
    def __init__(self, tool_calls, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _FakeLLM:
    """Replays a fixed script of responses and records what it was sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list[list] = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        idx = min(len(self.seen) - 1, len(self._responses) - 1)
        return self._responses[idx]


def _call(name, args, i=0):
    return {"name": name, "args": args, "id": f"c{i}"}


@pytest.fixture
def ws(tmp_path: Path):
    with Workspace(tmp_path / "root") as w:
        (w.path / "Foo.cs").write_text("line one\nline two\n", encoding="utf-8")
        yield w


@pytest.fixture
def payload():
    return ErrorPayload(exception_type="System.NullReferenceException", message="boom")


@pytest.fixture
def suspect():
    return SuspectSite(repo_owner="o", repo_name="r", file_path="Foo.cs", line=1)


def test_stuck_on_repeated_identical_call(monkeypatch, ws, payload, suspect):
    repeated = _Resp([_call("read_file", {"path": "Foo.cs"})])
    fake = _FakeLLM([repeated])
    monkeypatch.setattr(agent_fix, "get_chat_model", lambda **kw: fake)

    result = agent_fix.run_fix_agent(ws=ws, payload=payload, suspect=suspect)

    assert result.failure_reason == "stuck: identical tool call repeated"
    # Must bail well before burning every turn.
    assert len(fake.seen) < agent_fix._MAX_ITERATIONS


def test_distinct_calls_are_not_flagged_as_stuck(monkeypatch, ws, payload, suspect):
    responses = [
        _Resp([_call("read_file", {"path": "Foo.cs", "start_line": n})])
        for n in range(1, 9)
    ] + [_Resp([], content="giving up")]
    fake = _FakeLLM(responses)
    monkeypatch.setattr(agent_fix, "get_chat_model", lambda **kw: fake)

    result = agent_fix.run_fix_agent(ws=ws, payload=payload, suspect=suspect)
    assert result.failure_reason != "stuck: identical tool call repeated"


def test_wrapup_notice_is_injected_before_the_cap(monkeypatch, ws, payload, suspect):
    # Never calls done, so the loop runs to the cap.
    responses = [
        _Resp([_call("read_file", {"path": "Foo.cs", "start_line": n})])
        for n in range(1, agent_fix._MAX_ITERATIONS + 1)
    ]
    fake = _FakeLLM(responses)
    monkeypatch.setattr(agent_fix, "get_chat_model", lambda **kw: fake)

    agent_fix.run_fix_agent(ws=ws, payload=payload, suspect=suspect)

    sent = [
        m for turn in fake.seen for m in turn
        if "turn(s) left" in str(getattr(m, "content", ""))
    ]
    assert sent, "model was never told it was running out of turns"


def test_wrapup_notice_sent_once_not_every_turn(monkeypatch, ws, payload, suspect):
    responses = [
        _Resp([_call("read_file", {"path": "Foo.cs", "start_line": n})])
        for n in range(1, agent_fix._MAX_ITERATIONS + 1)
    ]
    fake = _FakeLLM(responses)
    monkeypatch.setattr(agent_fix, "get_chat_model", lambda **kw: fake)

    agent_fix.run_fix_agent(ws=ws, payload=payload, suspect=suspect)

    final = fake.seen[-1]
    notices = [m for m in final if "turn(s) left" in str(getattr(m, "content", ""))]
    assert len(notices) == 1


def test_edits_survive_hitting_the_cap(monkeypatch, ws, payload, suspect):
    """The point of the wrap-up: a capped run must still report its edits."""
    script = [
        _Resp([_call("read_file", {"path": "Foo.cs"})]),
        _Resp([_call("edit_file", {"path": "Foo.cs", "old_string": "line one",
                                   "new_string": "fixed one"})]),
    ] + [
        _Resp([_call("read_file", {"path": "Foo.cs", "start_line": n})])
        for n in range(1, agent_fix._MAX_ITERATIONS)
    ]
    fake = _FakeLLM(script)
    monkeypatch.setattr(agent_fix, "get_chat_model", lambda **kw: fake)

    result = agent_fix.run_fix_agent(ws=ws, payload=payload, suspect=suspect)
    assert result.changed_files == ["Foo.cs"]
    assert result.success is True
