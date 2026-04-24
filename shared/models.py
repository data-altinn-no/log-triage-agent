from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
Category = Literal[
    "exception",
    "http-5xx",
    "timeout",
    "dependency-failure",
    "auth",
    "data-integrity",
    "unknown",
]


class ErrorPayload(BaseModel):
    """Already-redacted error payload extracted from a GitHub issue body."""

    exception_type: str | None = None
    message: str | None = None
    stack_trace: str | None = None
    cloud_role: str | None = None
    operation: str | None = None
    request_path: str | None = None
    timestamp: str | None = None
    correlation_id: str | None = None
    raw: str = ""


class TriageResult(BaseModel):
    category: Category = "unknown"
    severity: Severity = "medium"
    fingerprint: str
    suggested_title: str
    summary: str
    root_cause_hypothesis: str = ""
    suggested_owner: str | None = None
    related_issue_numbers: list[int] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of: int | None = None
