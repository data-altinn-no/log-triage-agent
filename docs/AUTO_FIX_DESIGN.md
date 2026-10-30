# Auto-fix design — from triage to pull request

**Status:** implemented. Off until `AUTOFIX_ENABLED=true`.
**Scope:** the agent's core workflow — receive a production error, open a
triage issue, propose a fix, and raise a pull request.

## Graph

```
parse → fingerprint → dedupe → enrich ─┐
                                       │
                            ┌──────────┴──────────┐
                            │  branch decision    │
                            └──────┬───────┬──────┘
                          fix │            │ duplicate (or autofix suspended)
                              ▼            ▼
                           locate       publish
                              │            ▲
                              ▼            │
                             plan          │
                              │            │
                              ▼            │
                             fix  ─────────┘
                     (clone, apply, test, push, open PR)
                              │
                              ▼
                           publish     (issue links to the opened PR)
```

The issue-publishing path always runs. If any step in the fix branch fails
(no parseable frame, LLM flags the change as high-risk, tests fail, push is
rejected), the state carries a `skipped_reason` through to `publish` so the
public issue documents why no PR was opened. No signal is ever lost.

## Nodes

Files: `agents/graph/nodes/{locate,plan,fix,publish}.py`.

### `locate`

- **Input:** `ErrorPayload`.
- **Output:** `SuspectSite { repo_owner, repo_name, file_path, line, symbol,
  confidence, evidence }`.

Pure text parsing of the stack trace (`agents/services/locator.py`). Handles
Python, .NET (Unix + Windows paths), Node, Java frame formats. Strips common
absolute-path prefixes (`/src/`, `/app/`, `/work/`) to get a repo-relative
path. Skips framework frames (`site-packages`, `node_modules`, `Microsoft.*`,
etc.) when picking the "application frame".

Confidence heuristic:

- repo-relative-looking path + line number → 0.7
- only a filename (Java-style) → 0.3

Below `AUTOFIX_MIN_CONFIDENCE` (default 0.5), skip the branch — the public
issue still gets published normally.

### `plan`

- **Input:** `SuspectSite` + `ErrorPayload`.
- **Output:** `ProposedPatch { diff, rationale, risk, changed_files, base_sha }`.

Runs a **tool-using edit loop**, not one-shot diff generation
(`agents/services/agent_fix.py`). The LLM gets three tools — `read_file`,
`edit_file`, `done` — mirroring Claude Code's FileRead/FileEdit semantics, and
iterates against a real checkout:

- `read_file` returns the file line-numbered. Files that exceed the read cap
  come back as a window centred on the suspect line, always carrying an
  explicit `TRUNCATED:` note with the total line count and paging instructions.
  A silent truncation would let the agent edit code it never saw.
- `edit_file` requires a prior `read_file` on the same path and a **unique**
  `old_string`, so an edit built on hallucinated context cannot apply.
- `done` ends the loop with a rationale.

Afterwards `plan` captures `git diff` from the workspace. Because the patch is
derived from real edits to real file contents, context lines match by
construction — this is what removed the hallucinated-context corruption that
single-shot diff generation produced. The commit the agent reasoned against is
recorded as `base_sha` so `fix` can pin to it.

Rejection gates:

- Agent ends without edits, or `done` reports no changed files → skip.
- Empty captured diff → skip.
- Diff size > `AUTOFIX_MAX_DIFF_LINES` → skip.

**Note:** the LLM self-rated `risk` gate described in earlier revisions no
longer exists — the edit loop returns no risk field, and `plan` records
`risk="medium"` unconditionally. Diff size and the human review gate are what
bound blast radius now. Restoring a real risk signal is a follow-up.

### `fix`

- **Input:** `ProposedPatch`.
- **Output:** `VerifyResult` + `pr_number` / `pr_url` on success.

Owns the full workspace lifecycle in one `with Workspace()` block so no
non-serializable objects ever cross LangGraph state boundaries:

1. Shallow clone (depth 50), **check out `patch.base_sha`**, configure bot
   identity. Pinning matters: without it a merge landing on the base branch
   between `plan` and `fix` makes an otherwise-correct patch fail to apply.
2. Create branch `auto-fix/<fingerprint>-<issue-number>`.
3. `git apply --index`, fall back to `git apply --3way` for minor drift.
4. Commit and push the branch via `x-access-token` URL.
5. Send a `verify-fix` repository dispatch to `log-triage`, whose workflow
   checks the branch out on a GitHub runner and runs `dotnet restore`, `build`
   and `test`, the same steps the shared deploy flow runs. The agent finds the
   run by its title and waits up to `AUTOFIX_VERIFY_TIMEOUT_S`.
6. On a green run, open a **draft** PR linked to the run, with labels
   `auto-fix`, `needs-review`, `severity:<x>`.

A red, unfinished or missing run deletes the branch and falls through to
`publish`, which still documents the error. Repo code never runs in the
agent's container.

### `publish`

Unchanged in spirit; the body now includes an `Auto-fix attempt` section
linking to the PR (or noting the `skipped_reason`).

## Shared state additions

```python
class TriageState(TypedDict, total=False):
    # existing ...
    autofix: AutoFixOutcome  # always present once the branch is entered
```

`AutoFixOutcome` (in `shared/models.py`) aggregates `SuspectSite`,
`ProposedPatch`, `VerifyResult`, `pr_number`, `pr_url`, plus a
`skipped_reason` for graceful-degradation logging.

## Workspace service

`agents/services/workspace.py` — context-manager `Workspace` that owns a
single temp dir under `AUTOFIX_WORKDIR`, cleaned up on exit (including on
exception). Subprocess-only; never imports git in-process.

Repo code is never built or run in the workspace. The workspace only clones,
applies the diff and pushes; building and testing happen in the `verify-fix`
workflow in `log-triage`, on a fresh GitHub runner with a read-only token.

## Configuration

All `AUTOFIX_*` settings live in `shared/config.py`:

| Setting | Purpose |
| --- | --- |
| `AUTOFIX_ENABLED` | Runtime kill-switch. Flip to `false` to suspend PR creation during incidents without redeploying. |
| `AUTOFIX_VERIFY_EVENT` / `AUTOFIX_VERIFY_WORKFLOW` | The dispatch event and workflow file in `log-triage` that build and test the branch. |
| `AUTOFIX_VERIFY_TIMEOUT_S` | How long to wait for the verify run. |
| `AUTOFIX_MAX_DIFF_LINES` | Hard cap on proposed diff size. |
| `AUTOFIX_WORKDIR` | Tmp root for checkouts. |
| `AUTOFIX_MIN_CONFIDENCE` | Locator confidence threshold. |
| `AUTOFIX_BLOCK_LABEL` | Label on the input issue that skips the fix branch (default `no-auto-fix`). |
| `AUTOFIX_GIT_USER_NAME` / `_EMAIL` | Commit author for the bot. |

## Failure modes and fallbacks

| Failure | Fallback |
| --- | --- |
| `locate` confidence below threshold | Publish issue only; record reason |
| LLM returns non-JSON / no diff | Publish issue only |
| LLM self-rates risk as `high` | Publish issue only |
| Diff exceeds line cap | Publish issue only |
| `git apply` fails (even with `--3way`) | Publish issue only |
| CI run fails, times out or never starts | Publish issue only; branch deleted |
| Push fails (auth, protected branch) | Publish issue only |
| PR creation fails | Publish issue only |

Pattern: **any failure downgrades to publish-only**. There is no regression
of today's baseline behaviour.

## Scope

Fixes go to the repo the issue was routed to, against its default branch, so
the GitHub App's installation decides where a PR can be opened. Every PR is a
draft that a person reviews and merges; a ruleset on default branches keeps the
App from pushing to them directly. `AUTOFIX_ENABLED=false` turns the fix branch
off everywhere at once, and the `no-auto-fix` label skips it for one issue.

## Safeguards

- **Branch protection on `main`** in the target repo requires human approval.
- **CODEOWNERS** enforced. Auto-fix PRs do not bypass review.
- **`no-auto-fix` label** on the input issue skips the fix branch for that
  one error (operational override for known-problematic signatures).
- **Runtime kill-switch** (`AUTOFIX_ENABLED=false`) suspends PR creation
  without redeploying.
- **Scoped token.** GitHub App or fine-grained PAT with `contents: write` +
  `pull_requests: write` only on the repos the App is installed on; no admin
  scope, so the agent cannot bypass branch protection.
- **Per-day rate limit.** Planned; currently bounded implicitly by the
  30-minute App Insights poll cadence and fingerprint-based deduplication.
  Treat that bound as weak: in practice a single upstream condition produced
  thousands of distinct fingerprints, so upstream dedup is not a rate limit.

### Prompt injection is the underlying threat model

Everything above is what makes the pipeline safe against a threat that is
otherwise structural: **the input is attacker-influenceable**. Anyone who can
cause a logged exception with a controlled message controls text that reaches
`agent_fix.py`'s user prompt — and that prompt drives a loop holding file-write
tools and a path to `git push`.

The controls that matter for this, specifically:

- Tests must pass locally before anything is pushed.
- The agent can only ever open a PR; humans merge, always.
- Branch protection and CODEOWNERS are enforced on the target repo.
- The token is scoped to the App's installation with no admin scope, so the
  agent cannot bypass branch protection.
- Tests run on a GitHub runner with a read-only token, never in the agent's
  container, so code it just wrote cannot read the agent's credentials.

None of these are optional hardening. They are the reason an
attacker-influenceable input is acceptable at all.

## Non-goals

- Cross-repo refactors.
- Schema migrations.
- Changes under `infra/`, CI configs, or secrets paths.
- Autonomous merging. Humans merge. Always.

## Follow-ups

Concrete work items that extend the current implementation:

- **In-loop replan.** On a failing test run, feed the output back to the
  planner for one bounded retry. `AUTOFIX_MAX_RETRIES` is plumbed as a setting
  but has no effect — `fix.py` breaks out of the attempt loop unconditionally.
- **Restore a risk signal.** The edit-loop rewrite dropped the LLM self-rated
  risk gate; `risk` is currently hardcoded to `medium`.
- **Single workspace across `plan` and `fix`.** The two nodes clone the target
  repo separately. Pinning to `base_sha` makes that correct, but it is still
  two clones and one avoidable `git apply`.
- **Containerized test execution** (hardening tier 2 above).
- **Per-repo, per-day PR cap** enforced in-process.
- **GitHub App** instead of PAT, with per-repo installation tokens.
- **Telemetry.** Structured `autofix.*` events are already emitted; wire
  them to Langfuse / Log Analytics for a dashboard of outcome rates.

## Open questions

- Does the redaction pipeline strip file paths from stack traces? Current
  `locator.py` depends on them surviving redaction — verify before Stage B.
- Should we require an approval label (e.g. `approve-auto-fix`) on the
  input issue before the fix branch runs, rather than opt-out via
  `no-auto-fix`? Strictly safer, adds latency.
