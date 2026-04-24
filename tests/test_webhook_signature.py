import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
SECRET = "test-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload() -> bytes:
    return json.dumps(
        {
            "action": "opened",
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
