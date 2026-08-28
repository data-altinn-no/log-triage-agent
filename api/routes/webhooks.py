import json

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from agents.graph.runner import run_triage
from api.security import verify_github_signature
from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _should_process(action: str, labels: list[str]) -> bool:
    settings = get_settings()
    if action not in {"opened", "reopened", "labeled"}:
        return False
    return bool(settings.triage_labels & set(labels))


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    background: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    body: bytes = Depends(verify_github_signature),
) -> dict:
    if x_github_event != "issues":
        return {"skipped": True, "reason": f"event={x_github_event}"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {exc}") from exc

    action = payload.get("action", "")
    issue = payload.get("issue") or {}
    number = issue.get("number")
    title = issue.get("title", "")
    issue_body = issue.get("body") or ""
    labels = [label.get("name") for label in issue.get("labels", []) if label.get("name")]

    if not number:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing issue.number")

    if not _should_process(action, labels):
        log.info("webhook.skipped", number=number, action=action, labels=labels)
        return {"skipped": True, "action": action}

    log.info("webhook.accepted", number=number, action=action)
    background.add_task(
        _run_safely,
        issue_number=number,
        title=title,
        body=issue_body,
        labels=labels,
    )
    return {"accepted": True, "number": number}


def _run_safely(*, issue_number: int, title: str, body: str, labels: list[str]) -> None:
    try:
        run_triage(issue_number=issue_number, title=title, body=body, labels=labels)
    except Exception as exc:  # noqa: BLE001
        log.exception("triage.failed", number=issue_number, error=str(exc))
