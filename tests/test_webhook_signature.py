import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from api.main import app
from shared.config import get_settings

client = TestClient(app)
SECRET = "test-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _repo() -> dict:
    return {"full_name": get_settings().input_full_repo}


def _payload() -> bytes:
    return json.dumps(
        {
            "action": "opened",
            "repository": _repo(),
            "issue": {
                "number": 1,
                "title": "t",
                "body": "### Exception\nX",
                "labels": [{"name": "auto-triage"}],
            },
        }
    ).encode()


def test_rejects_missing_signature():
    resp = client.post(
        "/webhooks/github",
        content=_payload(),
        headers={"X-GitHub-Event": "issues", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_rejects_bad_signature():
    resp = client.post(
        "/webhooks/github",
        content=_payload(),
        headers={
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
    )
    assert resp.status_code == 401


def test_accepts_valid_signature_but_skips_without_label(monkeypatch):
    body = json.dumps(
        {
            "action": "opened",
            "repository": _repo(),
            "issue": {"number": 2, "title": "t", "body": "x", "labels": []},
        }
    ).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"skipped": True, "action": "opened"}


def test_fails_closed_when_secret_is_unset(monkeypatch):
    """A missing secret must NOT be read as 'this is dev, let it through'.

    This endpoint drives a pipeline that can clone, edit and push to the target
    repo, so an unsigned request is attacker-controlled input to that pipeline.
    """
    from shared.config import Settings

    monkeypatch.setattr(
        "api.security.get_settings",
        lambda: Settings(github_webhook_secret="", dev_mode=False),
    )
    resp = client.post(
        "/webhooks/github",
        content=_payload(),
        headers={"X-GitHub-Event": "issues", "Content-Type": "application/json"},
    )
    assert resp.status_code == 500
    assert "GITHUB_WEBHOOK_SECRET" in resp.json()["detail"]


def test_unsigned_allowed_only_under_explicit_dev_mode(monkeypatch):
    from shared.config import Settings

    monkeypatch.setattr(
        "api.security.get_settings",
        lambda: Settings(github_webhook_secret="", dev_mode=True),
    )
    resp = client.post(
        "/webhooks/github",
        content=_payload(),
        headers={"X-GitHub-Event": "issues", "Content-Type": "application/json"},
    )
    assert resp.status_code == 202


def test_dev_mode_still_rejects_a_signed_request_with_no_secret(monkeypatch):
    from shared.config import Settings

    monkeypatch.setattr(
        "api.security.get_settings",
        lambda: Settings(github_webhook_secret="", dev_mode=True),
    )
    resp = client.post(
        "/webhooks/github",
        content=_payload(),
        headers={
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
    )
    assert resp.status_code == 500


def test_skips_non_issue_event():
    body = b"{}"
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 202
    assert resp.json()["skipped"] is True


def _post(body: bytes):
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body),
        },
    )


def _labelled(repo: dict) -> bytes:
    return json.dumps({
        "action": "opened",
        "repository": repo,
        "issue": {"number": 45, "title": "t", "body": "x", "labels": [{"name": "auto-triage"}]},
    }).encode()


def test_an_event_from_another_repo_is_never_processed(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.webhooks.run_triage", lambda **kw: calls.append(kw))
    resp = _post(_labelled({"full_name": "data-altinn-no/plugin-nsg"}))
    assert resp.json()["skipped"] is True and calls == []


def test_a_labelled_issue_in_the_input_repo_is_accepted(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.webhooks.run_triage", lambda **kw: calls.append(kw))
    resp = _post(_labelled(_repo()))
    assert resp.json() == {"accepted": True, "number": 45}
    assert calls and calls[0]["issue_number"] == 45
