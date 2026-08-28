"""Plan node: tool-using agent loop edits the suspect file directly.

The LLM gets read_file/edit_file tools (mirroring Claude Code's FileRead/FileEdit
semantics). After the loop, we capture `git diff` from the workspace as the patch
that fix_node will apply. This eliminates the hallucinated-context corruption that
single-shot diff generation suffered from — the patch is generated from real edits
to real file contents, so context lines are guaranteed to match.
"""

from __future__ import annotations

from pathlib import Path

from agents.graph.state import TriageState
from agents.services import github as gh
from agents.services.agent_fix import run_fix_agent
from agents.services.workspace import Workspace, WorkspaceError
from shared.config import get_settings
from shared.logging import get_logger
from shared.models import ProposedPatch

log = get_logger(__name__)


def plan_node(state: TriageState) -> TriageState:
    outcome = state["autofix"]
    if outcome.skipped_reason or not outcome.suspect:
        return {"autofix": outcome}

    settings = get_settings()
    suspect = outcome.suspect
    payload = state["payload"]

    diff: str = ""
    rationale: str = ""
    changed_files: list[str] = []
    base_sha: str | None = None

    try:
        with Workspace(Path(settings.autofix_workdir)) as ws:
            ws.clone(
                gh.autofix_clone_url(),
                base_branch=settings.autofix_base_branch,
                depth=50,
            )
            # Record the exact commit the agent reasons against, so `fix` can pin
            # its own checkout to it and the diff is guaranteed to apply.
            base_sha = ws.head_sha()
            agent_outcome = run_fix_agent(ws=ws, payload=payload, suspect=suspect)
            if not agent_outcome.success:
                outcome.skipped_reason = (
                    f"agent loop: {agent_outcome.failure_reason or 'no edits'}"
                )
                log.info("plan.agent_failed", reason=outcome.skipped_reason)
                return {"autofix": outcome}

            try:
                diff = ws.git_diff()
            except WorkspaceError as exc:
                outcome.skipped_reason = f"could not capture diff: {exc}"
                log.warning("plan.diff_capture_failed", error=str(exc))
                return {"autofix": outcome}

            rationale = agent_outcome.rationale
            changed_files = agent_outcome.changed_files
    except WorkspaceError as exc:
        outcome.skipped_reason = f"workspace setup failed: {exc}"
        log.warning("plan.workspace_failed", error=str(exc))
        return {"autofix": outcome}

    if not diff.strip():
        outcome.skipped_reason = "agent reported success but produced no diff"
        log.info("plan.empty_diff")
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
        rationale=rationale or "agent fix",
        risk="medium",
        changed_files=changed_files or [suspect.file_path],
        base_sha=base_sha,
    )
    log.info(
        "plan.proposed",
        changed_lines=changed_lines,
        files=outcome.patch.changed_files,
        base_sha=(base_sha or "")[:10],
    )
    return {"autofix": outcome}


def _count_changed_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )
