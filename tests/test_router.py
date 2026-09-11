import pytest

from agents.services import router
from agents.services.router import resolve_repo
from shared.config import get_settings
from shared.models import ErrorPayload


@pytest.fixture(autouse=True)
def _pinned_settings(monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT_OWNER", "data-altinn-no")
    monkeypatch.setenv("GITHUB_OUTPUT_REPO", "core")
    monkeypatch.setenv("REPO_ROUTES", "")
    monkeypatch.setattr(
        router, "org_repos", lambda owner: frozenset({"plugin-nav", "plugin-newthing"})
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_stack_trace_repo_beats_the_cloud_role_map():
    payload = ErrorPayload(
        cloud_role="func-dancore-prod",
        stack_trace=(
            "   at Altinn.Dan.Plugin.Kartverket.Main.Get() in "
            "/home/runner/work/plugin-kartverket/plugin-kartverket/src/Main.cs:line 702"
        ),
    )
    route = resolve_repo(payload)
    assert route.full_repo == "data-altinn-no/plugin-kartverket"
    assert route.source == "stack"


def test_role_the_convention_gets_wrong_uses_the_exception_map():
    route = resolve_repo(ErrorPayload(cloud_role="func-essvv-prod-prod"))
    assert route.full_repo == "data-altinn-no/plugin-statensvegvesen"
    assert route.source == "exception"


def test_new_function_following_the_convention_needs_no_configuration():
    route = resolve_repo(ErrorPayload(cloud_role="func-esnewthing-prod-prod"))
    assert route.full_repo == "data-altinn-no/plugin-newthing"
    assert route.source == "convention"


def test_convention_guess_for_a_repo_that_does_not_exist_is_not_used():
    route = resolve_repo(ErrorPayload(cloud_role="func-esnosuchrepo-prod-prod"))
    assert route.is_fallback


def test_staging_slot_routes_to_the_same_repo():
    route = resolve_repo(ErrorPayload(cloud_role="func-esnav-prod-prod-staging"))
    assert route.full_repo == "data-altinn-no/plugin-nav"


def test_role_matching_no_rule_falls_back_to_the_configured_repo():
    route = resolve_repo(ErrorPayload(cloud_role="func-danwhatever-prod"))
    assert route.full_repo == "data-altinn-no/core"
    assert route.is_fallback


def test_config_routes_override_everything_without_a_deploy(monkeypatch):
    monkeypatch.setenv("REPO_ROUTES", "func-essvv-prod-prod=plugin-svv-v2")
    get_settings.cache_clear()
    route = resolve_repo(ErrorPayload(cloud_role="func-essvv-prod-prod"))
    assert route.full_repo == "data-altinn-no/plugin-svv-v2"
    assert route.source == "config"


def test_windows_style_frames_still_resolve():
    payload = ErrorPayload(
        stack_trace=r"at Foo.Bar() in D:\a\work\plugin-trad\plugin-trad\src\Foo.cs:line 12"
    )
    assert resolve_repo(payload).full_repo == "data-altinn-no/plugin-trad"


def test_empty_payload_does_not_raise():
    assert resolve_repo(ErrorPayload()).is_fallback
