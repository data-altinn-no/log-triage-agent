"""Plan node: LLM proposes a unified diff for the suspect file."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agents.graph.state import TriageState
from agents.prompts.plan import SYSTEM_PROMPT, USER_TEMPLATE
from agents.services import github as gh
from agents.services.llm import get_chat_model
from agents.services.workspace import Workspace, WorkspaceError
from shared.config import get_settings
from shared.logging import get_logger
from shared.models import ProposedPatch

log = get_logger(__name__)

_FILE_READ_CAP = 120_000  # bytes, to keep prompt bounded


def plan_node(state: TriageState) -> TriageState:
    outcome = state["autofix"]
    if outcome.skipped_reason or not outcome.suspect:
        return {"autofix": outcome}

    settings = get_settings()
    suspect = outcome.suspect
    payload = state["payload"]

    # Pull the file contents straight from GitHub via a shallow checkout.
    # (Cheaper than cloning the whole repo just to read one file.)
    try:
        with Workspace(Path(settings.autofix_workdir)) as ws:
            ws.clone(gh.autofix_clone_url(), base_branch=settings.autofix_base_branch, depth=1)
            try:
                file_contents = ws.read_file(suspect.file_path, max_bytes=_FILE_READ_CAP)
            except WorkspaceError as exc:
                outcome.skipped_reason = f"could not read suspect file: {exc}"
                log.info("plan.file_read_failed", error=str(exc))
                return {"autofix": outcome}
    except WorkspaceError as exc:
        outcome.skipped_reason = f"workspace setup failed: {exc}"
        log.warning("plan.workspace_failed", error=str(exc))
        return {"autofix": outcome}

    user_msg = USER_TEMPLATE.format(
        exception_type=payload.exception_type or "unknown",
        message=payload.message or "",
        operation=payload.operation or "",
        stack_trace=(payload.stack_trace or "")[:4000],
        file_path=suspect.file_path,
        line=suspect.line or "?",
        file_contents=file_contents,
    )

    llm = get_chat_model(temperature=0.0)
    resp = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_msg)])
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _parse_json_strict(content)
    if data is None:
        outcome.skipped_reason = "planner returned non-JSON output"
        log.warning("plan.invalid_json", raw=content[:500])
        return {"autofix": outcome}

    diff = (data.get("diff") or "").strip()
    risk = data.get("risk") or "medium"

    if risk == "high" or not diff:
        outcome.skipped_reason = f"planner flagged risk={risk}"
        log.info("plan.rejected", risk=risk, has_diff=bool(diff))
        return {"autofix": outcome}

    changed_lines = _count_changed_lines(diff)
    if changed_lines > settings.autofix_max_diff_lines:
        outcome.skipped_reason = (
            f"diff too large: {changed_lines} > {settings.autofix_max_diff_lines}"
        )
        log.info("plan.too_large", changed_lines=changed_lines)
        return {"autofix": outcome}

    outcome.patch = ProposedPatch(
        diff=diff,
        rationale=data.get("rationale", ""),
        risk=risk,
        changed_files=data.get("changed_files") or [suspect.file_path],
    )
    log.info(
        "plan.proposed",
        risk=risk,
        changed_lines=changed_lines,
        files=outcome.patch.changed_files,
    )
    return {"autofix": outcome}


def _parse_json_strict(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _count_changed_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )
