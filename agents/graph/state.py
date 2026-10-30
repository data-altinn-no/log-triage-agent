from typing import TypedDict

from shared.models import AutoFixOutcome, CodeProvenance, ErrorPayload, TriageResult


class TriageState(TypedDict, total=False):
    issue_number: int
    issue_title: str
    issue_body: str
    issue_labels: list[str]
    payload: ErrorPayload
    result: TriageResult
    autofix: AutoFixOutcome
    output_repo: str
    autofix_base_branch: str
    provenance: CodeProvenance
    error: str
