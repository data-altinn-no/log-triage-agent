# Hosting

Two Azure resources to deploy, plus one registry.

| Component                                                                                        | Runs on                                                           | Why                                                  |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- | ---------------------------------------------------- |
| [`function-log-monitor`](https://github.com/data-altinn-no/function-log-monitor) (separate repo) | **Azure Function App** (Linux, .NET 8 isolated, Consumption plan) | Timer trigger, pay-per-execution, trivial to operate |
| `log-triage-agent` (this repo)                                                                   | **Azure Container Apps** (consumption)                            | FastAPI + LangGraph, scale-to-zero, HTTPS ingress    |
| Container image                                                                                  | **Azure Container Registry** (Basic tier)                         | ~5 USD/month, required for Container Apps            |

Approximate monthly cost at low traffic: **< 10 USD total**.

## One-time setup

```bash
# Variables
RG=log-triage-rg
LOC=norwayeast
ACR=logtriageacr$RANDOM         # must be globally unique
FUNC=log-monitor-poller
APP=log-triage-agent
APPINS=<existing-app-insights-resource-id>

az group create -n $RG -l $LOC

# Container registry
az acr create -g $RG -n $ACR --sku Basic --admin-enabled true

# Build and push the agent image
az acr build -r $ACR -t log-triage-agent:latest .
```

## Deploy the agent (Container Apps)

```bash
az containerapp env create -g $RG -n log-triage-env -l $LOC

az containerapp create \
  -g $RG -n $APP \
  --environment log-triage-env \
  --image $ACR.azurecr.io/log-triage-agent:latest \
  --registry-server $ACR.azurecr.io \
  --ingress external --target-port 8081 \
  --min-replicas 0 --max-replicas 3 \
  --secrets \
      github-token=<PAT> \
      webhook-secret=<SECRET> \
      azure-openai-key=<KEY> \
  --env-vars \
      GITHUB_TOKEN=secretref:github-token \
      GITHUB_WEBHOOK_SECRET=secretref:webhook-secret \
      AZURE_OPENAI_API_KEY=secretref:azure-openai-key \
      AZURE_OPENAI_ENDPOINT=https://<your>.openai.azure.com/ \
      AZURE_OPENAI_DEPLOYMENT=gpt-4o \
      GITHUB_INPUT_OWNER=data-altinn-no \
      GITHUB_INPUT_REPO=core-triage \
      GITHUB_OUTPUT_OWNER=data-altinn-no \
      GITHUB_OUTPUT_REPO=core
```

Grab the URL:

```bash
az containerapp show -g $RG -n $APP --query properties.configuration.ingress.fqdn -o tsv
```

Then on GitHub `data-altinn-no/core-triage`:
**Settings → Webhooks → Add webhook**

- Payload URL: `https://<fqdn>/webhooks/github`
- Content type: `application/json`
- Secret: same as `GITHUB_WEBHOOK_SECRET`
- Events: **Issues** only.

## Deploy the poller (Function App)

```bash
az storage account create -g $RG -n danagentstore$RANDOM -l $LOC --sku Standard_LRS
az functionapp create \
  -g $RG -n $FUNC \
  --storage-account danagentstore$RANDOM \
  --consumption-plan-location $LOC \
  --runtime python --runtime-version 3.11 \
  --functions-version 4 \
  --os-type Linux

az functionapp config appsettings set -g $RG -n $FUNC --settings \
  APPINSIGHTS_APP_ID=<app-insights-application-id> \
  APPINSIGHTS_API_KEY=<app-insights-api-key> \
  GITHUB_TOKEN=<PAT> \
  GITHUB_INPUT_OWNER=data-altinn-no \
  GITHUB_INPUT_REPO=core-triage \
  TRIAGE_LABEL=auto-triage \
  LOOKBACK_MINUTES=30

# Deploy from the sibling repo (function-log-monitor):
git clone https://github.com/data-altinn-no/function-log-monitor.git
cd function-log-monitor
dotnet publish -c Release -o ./publish
cd publish && func azure functionapp publish $FUNC
```

## Local dev

Run the agent:

```bash
uvicorn api.main:app --reload --port 8081
# expose to GitHub via ngrok for webhook testing:
ngrok http 8081
```

Run the function locally:

```bash
# In the function-log-monitor repo:
cp local.settings.json.example local.settings.json   # fill in values
dotnet build
func start
```

## Security notes

- Use a **GitHub App** (not a PAT) in prod. Grant `Issues: Read & write` on both
  repos, install it only there.
- Prefer **Managed Identity** for App Insights: swap `APPINSIGHTS_API_KEY` for
  `DefaultAzureCredential` + `azure-monitor-query`. The function app needs
  `Log Analytics Reader` on the AI workspace.
- Store all secrets in **Container Apps secrets** or **Key Vault references**,
  never in plain env vars in a deployment pipeline.
- Lock down the Container App ingress with a GitHub IP allowlist if you don't
  need it reachable from elsewhere.
