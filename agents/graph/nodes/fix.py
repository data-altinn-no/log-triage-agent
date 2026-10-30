"""fix node: apply the proposed diff, push a branch, have CI test it, open a draft PR.

Building runs on a GitHub runner, never here, so repo code never sees the agent's keys.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from agents.graph.state import TriageState
from agents.services import github as gh
from agents.services.workspace import Workspace, WorkspaceError
from shared.config import get_settings
from shared.logging import get_logger
from shared.models import VerifyResult

log = get_logger(__name__)

_POLL_S = 15


def fix_node(state: TriageState) -> TriageState:
    outcome = state["autofix"]
    if outcome.skipped_reason or not outcome.patch:
        return {"autofix": outcome}

    settings = get_settings()
    target = state["output_repo"]
    base = state["autofix_base_branch"]
    verify = VerifyResult()
    outcome.verify = verify
    branch = _branch_name(state)

    with Workspace(Path(settings.autofix_workdir)) as ws:
        try:
            ws.clone(gh.autofix_clone_url(target), base_branch=base, depth=50)
            # Pin to the commit `plan` generated the diff against. Without this a
            # merge landing on the base branch between plan and fix would make an
            # otherwise-correct patch fail to apply.
            if outcome.patch.base_sha:
                ws.checkout_sha(outcome.patch.base_sha)
            ws.configure_identity(
                settings.autofix_git_user_name,
                settings.autofix_git_user_email,
            )
            ws.create_branch(branch)
        except WorkspaceError as exc:
            outcome.skipped_reason = f"workspace setup failed: {exc}"
            log.warning("fix.workspace_failed", error=str(exc))
            return {"autofix": outcome}

        try:
            ws.apply_diff(outcome.patch.diff)
            verify.applied = True
        except WorkspaceError as exc:
            verify.failure_reason = f"apply failed: {exc}"
            outcome.skipped_reason = verify.failure_reason
            log.info("fix.apply_failed", error=str(exc), diff=outcome.patch.diff)
            return {"autofix": outcome}

        try:
            sha = ws.commit(_commit_message(state))
            ws.push_branch(branch, gh.autofix_clone_url(target))
        except WorkspaceError as exc:
            outcome.skipped_reason = f"commit/push failed: {exc}"
            log.warning("fix.push_failed", error=str(exc))
            return {"autofix": outcome}

    run = _verify_in_ci(target, branch, settings.autofix_verify_timeout_s)
    verify.run_url = run.html_url if run else None
    verify.tests_passed = bool(run and run.conclusion == "success")
    if not verify.tests_passed:
        verify.failure_reason = (
            f"CI {run.conclusion or 'did not finish'}: {run.html_url}" if run
            else "CI run never started"
        )
        outcome.skipped_reason = f"tests failed in CI ({verify.failure_reason})"
        log.info("fix.tests_failed", reason=verify.failure_reason, diff=outcome.patch.diff)
        try:
            gh.delete_branch(target, branch)
        except Exception as exc:  # noqa: BLE001
            log.warning("fix.branch_cleanup_failed", branch=branch, error=str(exc))
        return {"autofix": outcome}

    try:
        pr = gh.open_autofix_pr(
            full_repo=target,
            base=base,
            branch=branch,
            title=_pr_title(state),
            body=_render_pr_body(state, verify),
            labels=[
                "auto-fix",
                "needs-review",
                f"severity:{state['result'].severity}",
            ],
        )
    except Exception as exc:  # noqa: BLE001
        outcome.skipped_reason = f"PR creation failed: {exc}"
        log.warning("fix.pr_failed", error=str(exc))
        return {"autofix": outcome}

    outcome.pr_number = pr.number
    outcome.pr_url = pr.html_url
    log.info("fix.pr_opened", pr=pr.number, branch=branch, commit=sha[:10], target=target)
    return {"autofix": outcome}


def _verify_in_ci(target: str, branch: str, timeout_s: int):
    """Dispatch the verify workflow and wait. Returns the run, or None if it never started."""
    request_id = uuid.uuid4().hex[:12]
    gh.dispatch_verify(full_repo=target, branch=branch, request_id=request_id)
    log.info("fix.verify_dispatched", target=target, branch=branch, request_id=request_id)
    deadline = time.monotonic() + timeout_s
    run = None
    while time.monotonic() < deadline:
        time.sleep(_POLL_S)
        run = gh.find_verify_run(request_id) or run
        if run is not None:
            run.update()
            if run.status == "completed":
                return run
    return run


# ---------- helpers ----------

def _branch_name(state: TriageState) -> str:
    fp = state["result"].fingerprint
    return f"auto-fix/{fp}-{state['issue_number']}"


def _commit_message(state: TriageState) -> str:
    result = state["result"]
    return (
        f"auto-fix: {result.suggested_title or 'production error'}\n\n"
        f"Fingerprint: {result.fingerprint}\n"
        f"Triage issue: private #{state['issue_number']}\n\n"
        f"Opened by log-triage-agent; human review required."
    )


def _pr_title(state: TriageState) -> str:
    base = state["result"].suggested_title or "Auto-fix for production error"
    return f"auto-fix: {base}"[:180]


def _render_pr_body(state: TriageState, verify: VerifyResult) -> str:
    outcome = state["autofix"]
    result = state["result"]
    payload = state["payload"]
    suspect = outcome.suspect
    patch = outcome.patch

    suspect_block = (
        f"- File: `{suspect.file_path}`\n"
        f"- Line: {suspect.line or '?'}\n"
        f"- Symbol: `{suspect.symbol or 'n/a'}`\n"
        f"- Locator confidence: {suspect.confidence:.2f}"
        if suspect
        else "_unknown_"
    )

    return f"""> ⚠️ **Opened by `log-triage-agent`.**
> Automatic PR from a production error signal. Review the diff and CI before merging.

## Triage summary

{result.summary or "_no summary_"}

**Root cause hypothesis:** {result.root_cause_hypothesis or "_unknown_"}
**Severity:** `{result.severity}` · **Category:** `{result.category}`

## Suspect site

{suspect_block}

## Change rationale

{(patch.rationale if patch else "_no rationale_")}

**Risk:** `{patch.risk if patch else "n/a"}`
**Changed files:** {", ".join(f"`{f}`" for f in (patch.changed_files if patch else [])) or "_n/a_"}

## Verification

Built and tested by CI: {verify.run_url}

## Links

- Cloud role: `{payload.cloud_role or "unknown"}`
- Operation: `{payload.operation or "unknown"}`
- Fingerprint: `{result.fingerprint}`
- Triage issue: private #{state['issue_number']}

<!-- fingerprint: {result.fingerprint} -->
"""
