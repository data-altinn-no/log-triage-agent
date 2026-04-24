# Azure / deployment

## Pipeline

```
App Insights
      │  (KQL, polled every 30 min)
      ▼
Azure Function (.NET 8, timer)  ──► data-altinn-no/core-triage (PRIVATE)
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

## Files

| File                                                                   | Purpose                                                                         |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| [`hosting.md`](hosting.md)                                             | **Start here.** Az CLI recipes to deploy both Function App and Container App    |
| [`kql/exceptions.kusto`](kql/exceptions.kusto)                         | Reference KQL query                                                             |
| [`redaction.md`](redaction.md)                                         | Redaction rules (kept in sync with `function-log-monitor/Services/Redactor.cs`) |
| [`logic-app/issue-body-template.md`](logic-app/issue-body-template.md) | Markdown template (kept in sync with `function-log-monitor/PollExceptions.cs`)  |

## Required GitHub setup

- **GitHub App or fine-grained PAT** with `Issues: write` on BOTH
  `data-altinn-no/core-triage` and `data-altinn-no/core`.
- A webhook on `core-triage` → `https://<container-app-fqdn>/webhooks/github`,
  content-type `application/json`, shared secret matching
  `GITHUB_WEBHOOK_SECRET`, event **Issues** only.
