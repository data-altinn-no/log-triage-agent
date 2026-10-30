from shared.tracing import langchain_config


def test_config_carries_run_name_and_metadata_without_callbacks_when_disabled():
    config = langchain_config(run_name="triage #1", issue_number=1)

    assert config == {"run_name": "triage #1", "metadata": {"issue_number": 1}}


def test_config_omits_run_name_when_not_given():
    assert "run_name" not in langchain_config(issue_number=1)
