from shared.tracing import langchain_config


def test_config_carries_run_name_and_metadata_without_callbacks_when_disabled():
    config = langchain_config(run_name="triage #1", issue_number=1)

    assert config == {"run_name": "triage #1", "metadata": {"issue_number": 1}}


def test_config_omits_run_name_when_not_given():
    assert "run_name" not in langchain_config(issue_number=1)


def test_tracing_switched_off_says_so_in_the_log(capsys):
    from shared.tracing import _handler

    _handler.cache_clear()
    assert _handler() is None
    assert "langfuse.off" in capsys.readouterr().out


def test_session_and_trace_name_reach_the_generation_not_just_the_root(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.runnables import RunnableLambda
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from agents.graph import runner
    from shared import tracing

    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="http://127.0.0.1:9",
        span_exporter=exporter,
    )
    monkeypatch.setattr(tracing, "_handler", lambda: CallbackHandler(public_key="pk-lf-test"))

    llm = FakeListChatModel(responses=["ok"])
    graph = RunnableLambda(lambda state: llm.invoke(state["issue_title"]))
    monkeypatch.setattr(runner, "get_graph", lambda: graph)

    runner.run_triage(issue_number=7, title="x" * 300, body="", labels=[])
    client.flush()

    spans = {s.attributes["langfuse.observation.type"]: s for s in exporter.get_finished_spans()}
    for span in (spans["chain"], spans["generation"]):
        assert span.attributes["session.id"] == "log-triage#7"
        assert span.attributes["langfuse.trace.name"] == "triage #7"
        assert span.attributes["langfuse.trace.metadata.issue_title"] == f'"{"x" * 190}"'
