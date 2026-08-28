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
        autofix_enabled=True,
        autofix_target_owner="data-altinn-no",
        autofix_target_repo="plugin-tilda",
    )
    base.update(overrides)
    return Settings(**base)


def _result(**overrides) -> TriageResult:
    base = dict(fingerprint="abc123", suggested_title="t", summary="s", is_duplicate=False)
    base.update(overrides)
    return TriageResult(**base)


def _patch(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr("agents.graph.runner.get_settings", lambda: settings)


def test_routes_to_locate_when_fully_configured(monkeypatch):
    _patch(monkeypatch, _settings())
    assert _route_after_enrich({"result": _result()}) == "locate"


def test_kill_switch_skips_the_fix_branch(monkeypatch):
    _patch(monkeypatch, _settings(autofix_enabled=False))
    assert _route_after_enrich({"result": _result()}) == "publish"


def test_duplicate_skips_the_fix_branch(monkeypatch):
    _patch(monkeypatch, _settings())
    assert _route_after_enrich({"result": _result(is_duplicate=True)}) == "publish"


@pytest.mark.parametrize(
    "overrides",
    [
        {"autofix_target_owner": ""},
        {"autofix_target_repo": ""},
        {"autofix_target_owner": "", "autofix_target_repo": ""},
    ],
)
def test_unconfigured_target_repo_skips_the_fix_branch(monkeypatch, overrides):
    _patch(monkeypatch, _settings(**overrides))
    assert _route_after_enrich({"result": _result()}) == "publish"


def test_missing_result_still_routes_to_locate(monkeypatch):
    # No enrichment result is not, by itself, a reason to skip the fix branch;
    # locate applies its own confidence gate.
    _patch(monkeypatch, _settings())
    assert _route_after_enrich({}) == "locate"


def test_kill_switch_wins_over_everything(monkeypatch):
    _patch(monkeypatch, _settings(autofix_enabled=False, autofix_target_repo=""))
    assert _route_after_enrich({"result": _result(is_duplicate=True)}) == "publish"
