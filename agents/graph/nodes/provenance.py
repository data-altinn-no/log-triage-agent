"""Find the commit that was running and whether the failing method has changed since.

Read-only and independent of auto-fix, so triage-only issues get it too.
"""

from __future__ import annotations

from agents.graph.state import TriageState
from agents.services import deployed_commit
from agents.services import github as gh
from agents.services.locator import extract_frames, pick_best_frame
from agents.services.router import resolve_repo
from shared.logging import get_logger
from shared.models import CodeProvenance

log = get_logger(__name__)


def provenance_node(state: TriageState) -> TriageState:
    result = state.get("result")
    if result is not None and result.is_duplicate:
        return {}
    payload = state["payload"]
    frame = pick_best_frame(extract_frames(payload.stack_trace or ""))
    if frame is None or not frame.line or "/" not in frame.file_path:
        return {}

    full_repo = state.get("output_repo") or resolve_repo(payload).full_repo
    try:
        repo = gh.client().get_repo(full_repo)
        branch = repo.default_branch
        head = repo.get_branch(branch).commit.sha
        pick = deployed_commit.resolve(
            repo, branch=branch, timestamp=payload.timestamp,
            file_path=frame.file_path, line=frame.line, symbol=frame.symbol,
        )
        changed = []
        if pick.verified and pick.sha and pick.sha != head:
            changed = deployed_commit.changes_since(
                repo, sha=pick.sha, branch=branch,
                file_path=frame.file_path, symbol=frame.symbol,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("provenance.failed", repo=full_repo, error=str(exc))
        return {}

    provenance = CodeProvenance(
        repo=full_repo, branch=branch, file_path=frame.file_path, line=frame.line,
        symbol=frame.symbol, deployed_sha=pick.sha, verified=pick.verified,
        reason=pick.reason, changed_on_branch=changed,
    )
    log.info(
        "provenance.resolved", repo=full_repo, sha=(pick.sha or "")[:10],
        verified=pick.verified, changed=len(changed),
    )
    return {"provenance": provenance}
