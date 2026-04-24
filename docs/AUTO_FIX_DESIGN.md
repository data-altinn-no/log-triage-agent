# Auto-fix design — from triage to pull request

**Status:** implemented. Rolling out via target-repo allowlist.
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
- **Output:** `ProposedPatch { diff, rationale, risk, changed_files }`.

Shallow-clones the target repo (depth 1) just to read the suspect file,
then asks the LLM for the smallest unified diff that plausibly fixes the
error. System prompt (`agents/prompts/plan.py`) is strict:

- Output must be JSON only.
- Diff must use the exact path given in the prompt.
- Touch the minimum number of lines; prefer guard clauses.
- No dependency additions, no config/CI/migration changes.
- Self-rate risk: `low` / `medium` / `high`.

Rejection gates:

- `risk == "high"` or empty diff → skip.
- Diff size > `AUTOFIX_MAX_DIFF_LINES` → skip.
- Non-JSON output → skip.

### `fix`

- **Input:** `ProposedPatch`.
- **Output:** `VerifyResult` + `pr_number` / `pr_url` on success.

Owns the full workspace lifecycle in one `with Workspace()` block so no
non-serializable objects ever cross LangGraph state boundaries:

1. Shallow clone (depth 50), configure bot identity.
2. Create branch `auto-fix/<fingerprint>-<issue-number>`.
3. `git apply --index`, fall back to `git apply --3way` for minor drift.
4. Run `AUTOFIX_TEST_CMD` with a scrubbed environment (only `PATH`, `HOME`,
   `LANG`, `LC_ALL`, `PWD`, `USER`, `SHELL`, `TMPDIR` survive).
5. Optional `AUTOFIX_LINT_CMD` — non-fatal, output attached to PR body.
6. Commit, push branch via `x-access-token` URL, open PR with labels
   `auto-fix`, `needs-review`, `severity:<x>`.

Tests must pass **locally** before any push. A failing test run closes the
branch without pushing and falls through to `publish`, which still documents
the error.

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

Hardening tiers, current → planned:

| Tier | Where we are | What it takes |
| --- | --- | --- |
| 1 — Unprivileged subprocess, scrubbed env | ✅ current | — |
| 2 — Containerized test runs (`docker run --network=none --read-only`) | planned | Wrap `ws.run_command` in a container call; add an `AUTOFIX_SANDBOX_IMAGE` setting |
| 3 — Per-attempt ACA job | future | Spawn a Container Apps job per fix; gives VM-level isolation at ~10s cold-start cost |

## Configuration

All `AUTOFIX_*` settings live in `shared/config.py`:

| Setting | Purpose |
| --- | --- |
| `AUTOFIX_ENABLED` | Runtime kill-switch. Flip to `false` to suspend PR creation during incidents without redeploying. |
| `AUTOFIX_TARGET_OWNER` / `AUTOFIX_TARGET_REPO` | Repo the agent writes PRs to. |
| `AUTOFIX_BASE_BRANCH` | Default `main`. |
| `AUTOFIX_TEST_CMD` | Test command. Must exit 0 for tests to count as passing. |
| `AUTOFIX_LINT_CMD` | Optional. Failures are non-fatal. |
| `AUTOFIX_TEST_TIMEOUT_S` | Per-command timeout. |
| `AUTOFIX_MAX_RETRIES` | Reserved for the replan loop (see below). |
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
| Tests fail | Publish issue only; no push |
| Push fails (auth, protected branch) | Publish issue only |
| PR creation fails | Publish issue only |

Pattern: **any failure downgrades to publish-only**. There is no regression
of today's baseline behaviour.

## Rollout (repo-level, not feature-level)

The implementation is complete. Rollout is scoped by which target repos
the agent is allowed to open PRs against. This is a deliberately narrow
allowlist, expanded as evidence accumulates.

1. **Stage A — single allowlisted repo.** `AUTOFIX_TARGET_REPO` points at
   one low-risk service. All `auto-fix` PRs reviewed by the service's
   CODEOWNERS. Metric of interest: merge rate and time-to-close.
2. **Stage B — broadened allowlist.** Add further repos once Stage A
   produces a meaningful merge rate and no incidents.
3. **Stage C — org-wide default.** Only after Stage B has matured.

The `AUTOFIX_ENABLED=false` default in `.env.example` protects against
accidental enablement in a half-configured environment; the intended
production posture is `AUTOFIX_ENABLED=true` with a scoped target repo.

## Safeguards

- **Branch protection on `main`** in the target repo requires human approval.
- **CODEOWNERS** enforced. Auto-fix PRs do not bypass review.
- **`no-auto-fix` label** on the input issue skips the fix branch for that
  one error (operational override for known-problematic signatures).
- **Runtime kill-switch** (`AUTOFIX_ENABLED=false`) suspends PR creation
  without redeploying.
- **Scoped token.** GitHub App or fine-grained PAT with `contents: write` +
  `pull_requests: write` only on the allowlisted repo; no admin scope, so
  the agent cannot bypass branch protection.
- **Per-day rate limit.** Planned; currently bounded implicitly by the
  30-minute App Insights poll cadence and fingerprint-based deduplication.

## Non-goals

- Cross-repo refactors.
- Schema migrations.
- Changes under `infra/`, CI configs, or secrets paths.
- Autonomous merging. Humans merge. Always.

## Follow-ups

Concrete work items that extend the current implementation:

- **In-loop replan.** On a failing test run, feed the output back to the
  planner for one bounded retry. `AUTOFIX_MAX_RETRIES` is already wired
  through; the loop just needs wiring in `fix.py`.
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
