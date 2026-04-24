from typing import TypedDict

from shared.models import ErrorPayload, TriageResult


class TriageState(TypedDict, total=False):
    issue_number: int
    issue_title: str
    issue_body: str
    issue_labels: list[str]
    payload: ErrorPayload
    result: TriageResult
    error: str
