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


RiskLevel = Literal["low", "medium", "high"]


class SuspectSite(BaseModel):
    """The file/location in the target repo likely responsible for the error."""

    repo_owner: str
    repo_name: str
    file_path: str
    line: int | None = None
    symbol: str | None = None
    confidence: float = 0.0  # 0..1
    evidence: str = ""  # human-readable reason (for logging/PR body)


class ProposedPatch(BaseModel):
    """Unified diff proposed by the LLM."""

    diff: str
    rationale: str
    risk: RiskLevel = "medium"
    changed_files: list[str] = Field(default_factory=list)


class VerifyResult(BaseModel):
    applied: bool = False
    tests_passed: bool = False
    attempts: int = 0
    last_output: str = ""  # truncated stdout/stderr for PR body
    failure_reason: str | None = None


class AutoFixOutcome(BaseModel):
    """End-state of the auto-fix branch. Attached to TriageState when attempted."""

    attempted: bool = False
    skipped_reason: str | None = None
    suspect: SuspectSite | None = None
    patch: ProposedPatch | None = None
    verify: VerifyResult | None = None
    pr_number: int | None = None
    pr_url: str | None = None
