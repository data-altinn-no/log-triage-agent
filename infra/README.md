# Azure / deployment

## Pipeline

```
App Insights
      │  (KQL, polled every 30 min)
      ▼
Azure Function (.NET 10, two timers)  ──► data-altinn-no/log-triage (PRIVATE)
      │  redacts + creates issue         │
      │                                   │  webhook (issues.opened)
      │                                   ▼
      │                              log-triage-agent  (Azure Container Apps)
      │                                   │
      │                                   ▼
      └──── audit trail ──►  data-altinn-no/core   (PUBLIC)
                                 created by agent, polished + deduped
```

> ⚠️ **All redaction happens inside Azure.** The private repo, the LLM, and the
> public repo only ever see already-redacted data. The public repo additionally
> receives only the agent-generated summary — never the raw stack trace.

## What runs where

| Component | Runs on | Deploy with |
| --- | --- | --- |
| `log-triage-agent` (this repo) | Azure Container Apps (consumption, scale-to-zero) | [`container-app.md`](container-app.md) |
| Container image | Azure Container Registry (Basic) | same |
| [`function-log-monitor`](https://github.com/data-altinn-no/function-log-monitor) | Azure Function App (Linux, .NET 10 isolated) | that repo's own README — it deploys from GitHub Actions on push to `main` |

Approximate cost at low traffic: **under 10 USD/month**.

## Required GitHub setup

- **GitHub App or fine-grained PAT** with `Issues: write` on BOTH
  `data-altinn-no/log-triage` and `data-altinn-no/core`. For the auto-fix
  branch, additionally `contents: write` + `pull_requests: write` on the
  allowlisted target repo only — no admin scope, so the agent cannot bypass
  branch protection.
- A webhook on `log-triage` → `https://<container-app-fqdn>/webhooks/github`,
  content-type `application/json`, shared secret matching
  `GITHUB_WEBHOOK_SECRET`, event **Issues** only.

## Local dev

```bash
uvicorn api.main:app --reload --port 8081
ngrok http 8081          # to receive real webhooks
```

Sign a test payload without a webhook:

```bash
python scripts/send_test_webhook.py --payload <issue-body.md>
python scripts/dry_run.py <issue-body.md>     # parse + locate only, no LLM
```

## Security notes

- Use a **GitHub App** rather than a PAT in production, installed only on the
  repos it needs.
- Prefer **Managed Identity** for App Insights: swap `APPINSIGHTS_API_KEY` for
  `DefaultAzureCredential` + `azure-monitor-query`, with `Log Analytics Reader`
  on the workspace.
- Secrets belong in **Key Vault references**, never plain env vars in a
  pipeline. See [`container-app.md`](container-app.md) §4.
