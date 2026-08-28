# log-triage-agent

AI agent that closes the loop from **production error to pull request**. It
receives errors from Azure (via GitHub issues created by
[`function-log-monitor`](https://github.com/data-altinn-no/function-log-monitor)),
triages and deduplicates them with an LLM, opens a polished issue in
[`data-altinn-no/core`](https://github.com/data-altinn-no/core), and — for
errors it can localize to a file — drafts a pull request with a proposed fix.

## Architecture

```
App Insights
     │ KQL (polled every 30 min)
     ▼
function-log-monitor   (.NET 10 Azure Function, separate repo)
     • redacts PII / secrets
     • creates raw triage issue
     ▼
data-altinn-no/log-triage     (PRIVATE repo — internal audit trail)
     │ webhook: issues.opened
     ▼
log-triage-agent               (FastAPI + LangGraph, Azure Container Apps)
     1. Verify HMAC
     2. Parse issue body
     3. Fingerprint
     4. Dedupe against PUBLIC repo
     5. LLM enrichment
     6. Auto-fix branch (skipped for duplicates):
          locate (stack-trace → file) → plan (edit loop → diff)
            → fix (clone / apply / test / push / open PR)
     7. Publish polished issue to PUBLIC repo, linking to the auto-fix PR
     8. Close the private issue, link to public
     ▼
data-altinn-no/core            (PUBLIC repo — clean, deduped, actionable)
```

**Two safety layers for sensitive data:**

1. Redaction runs in the Azure Function, **before** data leaves Azure.
2. The agent publishes only a high-level summary to the public repo —
   never the raw stack trace. The full (already-redacted) payload stays
   in the private repo as an audit trail.

## Tech

- Python 3.11+, FastAPI, LangGraph, LangChain
- Two LLM providers, selected by `LLM_PROVIDER`:
  - `azure_ai_foundry` — Anthropic Claude via the Messages API on Azure AI
    Foundry (`claude-sonnet-5` by default; `claude-opus-5` for the hardest
    fixes). This is the maintained path.
  - `azure_openai` — Azure-hosted OpenAI. Its deployment and API version
    predate the current surface; review before relying on it.
- PyGithub for GitHub API
- Langfuse for tracing (optional, off by default — `shared/tracing.py`)
- structlog for JSON logging

Dependencies are bounded in `requirements.txt` and pinned in
`requirements.lock.txt`; the Docker image installs from the lockfile.
Regenerate with `uv pip compile requirements.txt -o requirements.lock.txt`.

## Quick start

```bash
pip install -r requirements-dev.txt
cp .env.example .env
# fill in the LLM provider keys, GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET

uvicorn api.main:app --reload --port 8081
```

Send a test event:

```bash
python scripts/send_test_webhook.py --payload <issue-body.md>
```

Eval fixtures built from real production defects live in the private
[`log-triage`](https://github.com/data-altinn-no/log-triage) repo under
`eval/` — they contain production error content and cannot live here.

Run tests:

```bash
pytest
```

## Endpoints

| Method | Path               | Description                         |
| ------ | ------------------ | ----------------------------------- |
| POST   | `/webhooks/github` | GitHub issues webhook (HMAC-signed) |
| GET    | `/health`          | Health check                        |

## Auto-fix pipeline

The full loop from **production error → triage issue → pull request**.
Non-duplicate errors flow through `locate → plan → fix` after enrichment:

- **`locate`** parses the stack trace for a file + line (pure text, no LLM,
  no clone).
- **`plan`** shallow-clones the target repo and runs a tool-using edit loop —
  the LLM gets `read_file` / `edit_file` / `done` and edits the real checkout,
  then the resulting `git diff` is captured as the patch. Because edits are made
  against real file contents, context lines match by construction. The diff is
  size-capped.
- **`fix`** clones into a sandbox workspace, checks out the exact commit `plan`
  worked against, applies the diff, runs the configured test command with a
  scrubbed environment, commits, pushes a branch, and opens a PR that
  references the triage issue.

Tests must pass locally before anything is pushed, and the agent can only ever
open a PR — humans merge. That matters because the input is
attacker-influenceable: anyone who can trigger a logged exception with a
controlled message controls text that reaches the fix agent's prompt. See the
threat model in [`docs/AUTO_FIX_DESIGN.md`](docs/AUTO_FIX_DESIGN.md).

Configuration: set `AUTOFIX_TARGET_REPO` (and `AUTOFIX_TEST_CMD` if not
`pytest -q`), then `AUTOFIX_ENABLED=true`. Per-issue override: apply the
`no-auto-fix` label to the input issue. Operator override: flip
`AUTOFIX_ENABLED` to `false` at runtime to suspend PR creation during an
incident without redeploying.

Rollout is scoped by the target-repo allowlist, not by a feature flag — the
pipeline is always on, but it only opens PRs against the repos you point it
at. See [`docs/AUTO_FIX_DESIGN.md`](docs/AUTO_FIX_DESIGN.md) for the full
design, the hardening roadmap, and stage-gated allowlist rollout.

## Deployment

- [`infra/README.md`](infra/README.md) — what runs where, GitHub setup, local dev.
- [`infra/container-app.md`](infra/container-app.md) — the full Container Apps
  runbook: Foundry, Key Vault, managed identity, day-2 operations.

The Function App deploys from its own repo's GitHub Actions on push to `main`.

Redaction rules live in `function-log-monitor/Services/Redactor.cs`, covered
by its own test suite. There is no separate spec to keep in sync.

> **Not yet deployed.** `log-triage-agent` has no deployments and the auto-fix
> loop has not been run end-to-end against a real error. Treat the runbook as
> untested.

## Repository layout

```
log-triage-agent/
├── api/                     FastAPI app (webhook receiver)
│   ├── main.py
│   ├── security.py          HMAC SHA-256 verification
│   └── routes/
│       ├── health.py
│       └── webhooks.py
├── agents/
│   ├── graph/               LangGraph workflow
│   │   ├── state.py
│   │   ├── runner.py
│   │   └── nodes/           parse, fingerprint, dedupe, enrich,
│   │                        locate, plan, fix, publish
│   ├── services/
│   │   ├── github.py        dual-repo (input/output) issue ops
│   │   ├── llm.py           chat-model factory (Foundry/Claude or Azure OpenAI)
│   │   ├── agent_fix.py     read/edit tool loop that produces the patch
│   │   ├── workspace.py     sandboxed clone / apply / test / push
│   │   ├── locator.py       stack trace → suspect file+line
│   │   ├── parser.py        issue-body → ErrorPayload
│   │   └── fingerprint.py
│   └── prompts/             enrichment prompt
├── docs/                    auto-fix design
├── infra/                   hosting / deployment runbooks
├── shared/                  config, models, logging, tracing
├── tests/
├── scripts/
├── Dockerfile
├── pyproject.toml
├── requirements.txt         bounded direct deps
├── requirements.lock.txt    resolved pins (used by the image)
└── .env.example
```

## Related repos

- [`function-log-monitor`](https://github.com/data-altinn-no/function-log-monitor)
  — upstream .NET Azure Function that feeds this agent.
