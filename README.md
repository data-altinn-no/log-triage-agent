# log-triage-agent

AI agent that closes the loop from **production error to pull request**. It
receives errors from Azure (via GitHub issues created by
[`function-log-monitor`](https://github.com/data-altinn-no/function-log-monitor)),
triages and deduplicates them with Azure OpenAI, opens a polished issue in
[`data-altinn-no/core`](https://github.com/data-altinn-no/core), and — for
errors it can localize to a file — drafts a pull request with a proposed fix.

## Architecture

```
App Insights
     │ KQL (polled every 30 min)
     ▼
function-log-monitor   (.NET 8 Azure Function, separate repo)
     • redacts PII / secrets
     • creates raw triage issue
     ▼
data-altinn-no/core-triage     (PRIVATE repo — internal audit trail)
     │ webhook: issues.opened
     ▼
log-triage-agent               (FastAPI + LangGraph, Azure Container Apps)
     1. Verify HMAC
     2. Parse issue body
     3. Fingerprint
     4. Dedupe against PUBLIC repo
     5. LLM enrichment (Azure OpenAI)
     6. Auto-fix branch (skipped for duplicates):
          locate (stack-trace → file) → plan (LLM → diff)
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

- Python 3.11+, FastAPI, LangGraph, LangChain, Azure OpenAI
- PyGithub for GitHub API
- Langfuse for tracing (optional)
- structlog for JSON logging

## Quick start

```bash
pip install -r requirements-dev.txt
cp .env.example .env
# fill in AZURE_OPENAI_*, GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET

uvicorn api.main:app --reload --port 8081
```

Send a test event:

```bash
python scripts/send_test_webhook.py
```

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
- **`plan`** shallow-clones the target repo, reads the suspect file, and asks
  the LLM for the smallest unified diff that plausibly fixes the error. The
  diff is size-capped and rejected if the model flags it `risk=high`.
- **`fix`** clones into a sandbox workspace, applies the diff, runs the
  configured test command with a scrubbed environment, commits, pushes a
  branch, and opens a PR that references the triage issue.

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

- [`infra/hosting.md`](infra/hosting.md) — `az` CLI recipes to deploy to
  Azure Container Apps (the agent) and Azure Functions (the log monitor,
  in the sibling repo).
- [`infra/redaction.md`](infra/redaction.md) — canonical redaction rules,
  kept in sync with `function-log-monitor/Services/Redactor.cs`.

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
│   │   └── nodes/           parse, fingerprint, dedupe, enrich, publish
│   ├── services/
│   │   ├── github.py        dual-repo (input/output) issue ops
│   │   ├── llm.py           Azure OpenAI client
│   │   ├── parser.py        issue-body → ErrorPayload
│   │   └── fingerprint.py
│   └── prompts/
├── infra/                   hosting + KQL + redaction docs
├── shared/                  config, models, logging
├── tests/
├── scripts/
├── Dockerfile
├── pyproject.toml
├── requirements.txt
└── .env.example
```

## Related repos

- [`function-log-monitor`](https://github.com/data-altinn-no/function-log-monitor)
  — upstream .NET Azure Function that feeds this agent.
