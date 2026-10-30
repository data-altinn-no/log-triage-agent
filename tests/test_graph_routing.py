"""Tests for the enrich -> (locate | publish) branch decision.

Every one of these conditions is a safety gate on whether the agent is allowed
to open a PR against someone else's repository.
"""

import pytest

from agents.graph.runner import _route_after_enrich
from shared.config import Settings, get_settings
from shared.models import TriageResult


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(**overrides) -> Settings:
    base = dict(
        autofix_enabled=True, autofix_repos="plugin-tilda", github_output_owner="data-altinn-no"
    )
    base.update(overrides)
    return Settings(**base)


def _result(**overrides) -> TriageResult:
    base = dict(fingerprint="abc123", suggested_title="t", summary="s", is_duplicate=False)
    base.update(overrides)
    return TriageResult(**base)


def _state(repo="data-altinn-no/plugin-tilda", **result) -> dict:
    return {"result": _result(**result), "output_repo": repo}


def _patch(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr("agents.graph.runner.get_settings", lambda: settings)


def test_routes_to_locate_when_the_routed_repo_is_allowed(monkeypatch):
    _patch(monkeypatch, _settings())
    assert _route_after_enrich(_state()) == "locate"


def test_kill_switch_skips_the_fix_branch(monkeypatch):
    _patch(monkeypatch, _settings(autofix_enabled=False))
    assert _route_after_enrich(_state()) == "publish"


def test_duplicate_skips_the_fix_branch(monkeypatch):
    _patch(monkeypatch, _settings())
    assert _route_after_enrich(_state(is_duplicate=True)) == "publish"


@pytest.mark.parametrize(
    "repos, routed",
    [
        ("", "data-altinn-no/plugin-tilda"),
        ("plugin-nav", "data-altinn-no/plugin-tilda"),
        ("plugin-tilda", "data-altinn-no/plugin-tilda-old"),
        ("plugin-tilda", "someone-else/plugin-tilda"),
    ],
)
def test_a_repo_outside_the_allowlist_skips_the_fix_branch(monkeypatch, repos, routed):
    _patch(monkeypatch, _settings(autofix_repos=repos))
    assert _route_after_enrich(_state(repo=routed)) == "publish"


def test_allowlist_accepts_full_names_spaces_and_case(monkeypatch):
    _patch(monkeypatch, _settings(autofix_repos=" core , Data-Altinn-No/Plugin-Tilda "))
    assert _route_after_enrich(_state(repo="data-altinn-no/plugin-tilda")) == "locate"
    assert _route_after_enrich(_state(repo="data-altinn-no/core")) == "locate"


def test_missing_result_still_routes_to_locate(monkeypatch):
    # No enrichment result is not, by itself, a reason to skip the fix branch;
    # locate applies its own confidence gate.
    _patch(monkeypatch, _settings())
    assert _route_after_enrich({"output_repo": "data-altinn-no/plugin-tilda"}) == "locate"


def test_kill_switch_wins_over_everything(monkeypatch):
    _patch(monkeypatch, _settings(autofix_enabled=False))
    assert _route_after_enrich(_state(is_duplicate=True)) == "publish"
