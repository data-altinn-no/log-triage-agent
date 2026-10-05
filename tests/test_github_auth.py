import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from github import Auth

from agents.services import github as gh
from shared.config import get_settings


@pytest.fixture
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture(autouse=True)
def _clear_caches():
    get_settings.cache_clear()
    gh._auth.cache_clear()
    gh.client.cache_clear()
    yield
    get_settings.cache_clear()
    gh._auth.cache_clear()
    gh.client.cache_clear()


def test_reads_pem_directly(pem):
    assert gh.read_private_key(pem).startswith("-----BEGIN")


def test_reads_base64_wrapped_pem(pem):
    assert gh.read_private_key(base64.b64encode(pem.encode()).decode()) == pem


def test_rejects_key_that_is_neither_pem_nor_base64():
    with pytest.raises(RuntimeError, match="neither PEM nor base64"):
        gh.read_private_key("nonsense!")


def test_app_settings_select_installation_auth(monkeypatch, pem):
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv23liTEST")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "157246034")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    assert isinstance(gh._auth(), Auth.AppInstallationAuth)


def test_falls_back_to_token_when_app_is_not_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "0")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    assert isinstance(gh._auth(), Auth.Token)


def test_no_credentials_at_all_is_an_error(monkeypatch):
    for name in (
        "GITHUB_APP_CLIENT_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_TOKEN",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "0")
    with pytest.raises(RuntimeError, match="no GitHub credentials"):
        gh._auth()


def test_partial_app_settings_do_not_count_as_configured(monkeypatch, pem):
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv23liTEST")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "0")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    assert isinstance(gh._auth(), Auth.Token)


def test_access_token_works_without_constructing_the_client_first(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "0")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    assert gh.access_token() == "ghp_example"
