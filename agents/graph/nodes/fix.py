"""fix node: apply the proposed diff in a workspace, run tests, push, open PR.

Owns the full workspace lifecycle so we never pass non-serializable objects
through LangGraph state.
"""

from __future__ import annotations

from pathlib import Path

from agents.graph.state import TriageState
from agents.services import github as gh
from agents.services.workspace import Workspace, WorkspaceError
from shared.config import get_settings
from shared.logging import get_logger
from shared.models import VerifyResult

log = get_logger(__name__)

_OUTPUT_TAIL = 4000  # chars retained for PR body


def fix_node(state: TriageState) -> TriageState:
    outcome = state["autofix"]
    if outcome.skipped_reason or not outcome.patch:
        return {"autofix": outcome}

    settings = get_settings()
    verify = VerifyResult()
    branch = _branch_name(state)

    with Workspace(Path(settings.autofix_workdir)) as ws:
        try:
            ws.clone(
                gh.autofix_clone_url(),
                base_branch=settings.autofix_base_branch,
                depth=50,
            )
            ws.configure_identity(
                settings.autofix_git_user_name,
                settings.autofix_git_user_email,
            )
            ws.create_branch(branch)
        except WorkspaceError as exc:
            outcome.skipped_reason = f"workspace setup failed: {exc}"
            log.warning("fix.workspace_failed", error=str(exc))
            return {"autofix": outcome}

        # 1. Apply diff
        try:
            ws.apply_diff(outcome.patch.diff)
            verify.applied = True
        except WorkspaceError as exc:
            verify.failure_reason = f"apply failed: {exc}"
            outcome.verify = verify
            log.info("fix.apply_failed", error=str(exc))
            return {"autofix": outcome}

        # 2. Run tests (single attempt in v1; bounded retry is a follow-up)
        attempts = 0
        while attempts <= settings.autofix_max_retries:
            attempts += 1
            r = ws.run_command(
                settings.autofix_test_cmd,
                timeout_s=settings.autofix_test_timeout_s,
            )
            verify.last_output = r.combined[-_OUTPUT_TAIL:]
            if r.ok:
                verify.tests_passed = True
                break
            verify.failure_reason = (
                f"tests failed (exit {r.returncode}"
                f"{', timed out' if r.timed_out else ''})"
            )
            break  # no LLM replan loop yet
        verify.attempts = attempts

        if not verify.tests_passed:
            outcome.verify = verify
            log.info("fix.tests_failed", reason=verify.failure_reason, attempts=attempts)
            return {"autofix": outcome}

        # 3. Optional lint (non-fatal)
        if settings.autofix_lint_cmd:
            lr = ws.run_command(settings.autofix_lint_cmd, timeout_s=120)
            if not lr.ok:
                verify.last_output += "\n\n--- lint output ---\n" + lr.combined[-1000:]
                log.info("fix.lint_warn")

        # 4. Commit + push
        try:
            sha = ws.commit(_commit_message(state))
            ws.push_branch(branch, gh.autofix_clone_url())
        except WorkspaceError as exc:
            outcome.skipped_reason = f"commit/push failed: {exc}"
            log.warning("fix.push_failed", error=str(exc))
            outcome.verify = verify
            return {"autofix": outcome}

        # 5. Open PR
        try:
            pr = gh.open_autofix_pr(
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
            outcome.verify = verify
            log.warning("fix.pr_failed", error=str(exc))
            return {"autofix": outcome}

        outcome.verify = verify
        outcome.pr_number = pr.number
        outcome.pr_url = pr.html_url
        log.info(
            "fix.pr_opened",
            pr=pr.number,
            branch=branch,
            commit=sha[:10],
            target=settings.autofix_target_full_repo,
        )
        return {"autofix": outcome}


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

    test_tail = (verify.last_output or "_no output captured_")[-3500:]

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

- Tests passed locally: **{verify.tests_passed}**
- Attempts: {verify.attempts}

<details>
<summary>Last test output (truncated)</summary>

```
{test_tail}
```

</details>

## Links

- Cloud role: `{payload.cloud_role or "unknown"}`
- Operation: `{payload.operation or "unknown"}`
- Fingerprint: `{result.fingerprint}`
- Triage issue: private #{state['issue_number']}

<!-- fingerprint: {result.fingerprint} -->
"""
