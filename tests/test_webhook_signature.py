import hashlib
import hmac
import json
import threading

from fastapi.testclient import TestClient

from api.main import app
from api.routes.webhooks import _in_flight
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


def _event(action: str, number: int, *, added: str | None = None, state: str = "open") -> bytes:
    labels = ["prod", "source:app-insights", "auto-triage"]
    payload = {
        "action": action,
        "repository": _repo(),
        "issue": {
            "number": number,
            "title": "t",
            "body": "x",
            "state": state,
            "labels": [{"name": n} for n in labels],
        },
    }
    if added:
        payload["label"] = {"name": added}
    return json.dumps(payload).encode()


def test_opened_and_labeled_events_for_a_new_issue_start_one_run(monkeypatch):
    started, finish = threading.Event(), threading.Event()
    calls = []

    def slow_triage(**kw):
        calls.append(kw)
        started.set()
        finish.wait(5)

    monkeypatch.setattr("api.routes.webhooks.run_triage", slow_triage)
    opened = threading.Thread(target=_post, args=(_event("opened", 60),))
    opened.start()
    assert started.wait(5)

    responses = [
        _post(_event("labeled", 60, added=name)).json()
        for name in ("prod", "source:app-insights", "auto-triage")
    ]
    finish.set()
    opened.join(5)

    assert len(calls) == 1
    assert all(r["skipped"] for r in responses)
    assert responses[-1] == {"skipped": True, "reason": "in_flight"}


def test_labeled_with_a_non_triage_label_is_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.webhooks.run_triage", lambda **kw: calls.append(kw))
    resp = _post(_event("labeled", 61, added="prod"))
    assert resp.json() == {"skipped": True, "action": "labeled"} and calls == []


def test_labeled_with_the_triage_label_is_accepted(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.webhooks.run_triage", lambda **kw: calls.append(kw))
    resp = _post(_event("labeled", 62, added="auto-triage"))
    assert resp.json() == {"accepted": True, "number": 62} and len(calls) == 1


def test_a_closed_issue_is_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.webhooks.run_triage", lambda **kw: calls.append(kw))
    for action, added in (("reopened", None), ("labeled", "auto-triage")):
        resp = _post(_event(action, 63, added=added, state="closed"))
        assert resp.json()["skipped"] is True
    assert calls == []


def test_the_guard_is_released_after_a_run(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.webhooks.run_triage", lambda **kw: calls.append(kw))
    assert _post(_event("opened", 64)).json()["accepted"] is True
    assert _post(_event("opened", 64)).json()["accepted"] is True
    assert len(calls) == 2 and 64 not in _in_flight


def test_the_guard_is_released_after_a_run_that_raised(monkeypatch):
    def boom(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("api.routes.webhooks.run_triage", boom)
    assert _post(_event("opened", 65)).json()["accepted"] is True
    assert 65 not in _in_flight
    assert _post(_event("opened", 65)).json()["accepted"] is True
