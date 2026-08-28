# Azure Container App deployment

End-to-end runbook for deploying `log-triage-agent` to Azure Container Apps with
Azure AI Foundry (Anthropic Claude), a GitHub App for repo access, and Key Vault
for secrets.

The shape:

```
   GitHub repo (issues opened)
            │
            ▼  webhook
   ┌──────────────────────────┐
   │  Container App           │
   │   log-triage-agent       │ ── Anthropic Messages API ─▶  Azure AI Foundry
   │   (FastAPI + LangGraph)  │ ── Octokit/PyGithub ────────▶  GitHub App
   └──────────────────────────┘
            │ pulls secrets
            ▼
       Azure Key Vault
       (via Managed Identity)
```

## Prerequisites

- Azure subscription with permissions to create resource groups, Container Apps,
  Container Registry, Key Vault, and AI Foundry projects.
- Azure CLI 2.60+ (`az upgrade`).
- A GitHub App created at the org level, with `contents: write` and
  `pull_requests: write` on the target repo only.
  You'll need the App ID, installation ID, and the private-key `.pem` file.
- An Azure AI Foundry project with a Claude model deployed.

## 0. Variables

Set once in your shell — every command below references these:

```bash
RG=log-triage-rg
LOC=norwayeast
ACR=logtriageacr$RANDOM            # globally unique
KV=log-triage-kv$RANDOM            # globally unique
ENV=log-triage-env
APP=log-triage-agent
IMG_TAG=v1
```

## 1. Resource group + Container Registry

```bash
az group create -n $RG -l $LOC

az acr create -g $RG -n $ACR --sku Basic --admin-enabled false
# admin-enabled=false because we'll use managed identity for image pulls
```

## 2. Build and push the agent image

From the repo root:

```bash
az acr build -r $ACR -t log-triage-agent:$IMG_TAG .
```

This builds `Dockerfile` server-side in ACR (no local Docker needed).

## 3. Provision an Azure AI Foundry Anthropic deployment

In the Azure portal:

1. Open **Azure AI Foundry** → your project (or create one in `$RG`).
2. **Models + endpoints** → **Deploy model** → search for **Claude Sonnet 4.5** (or
   the Claude variant you want).
3. Accept the Anthropic terms.
4. After deployment, copy:
   - **Endpoint** — looks like `https://<resource>.services.ai.azure.com`
   - **Key** — under "Keys and Endpoint"
   - **Model name** — e.g. `claude-sonnet-5`, or `claude-opus-5` for the
     hardest fixes (or whatever deployment name you chose)

> **Important:** the agent needs the endpoint **suffixed with `/anthropic`** to
> hit the native Messages API. So if Foundry shows
> `https://my-foundry.services.ai.azure.com`, you store
> `https://my-foundry.services.ai.azure.com/anthropic` in `AZURE_AI_FOUNDRY_ENDPOINT`.

## 4. Key Vault for secrets

```bash
az keyvault create -g $RG -n $KV -l $LOC --enable-rbac-authorization true

# Stash secrets — repeat per secret. Replace the literal values.
az keyvault secret set --vault-name $KV --name foundry-key       --value "<paste-key>"
az keyvault secret set --vault-name $KV --name webhook-secret    --value "$(openssl rand -hex 32)"
az keyvault secret set --vault-name $KV --name gh-app-id         --value "<app-id>"
az keyvault secret set --vault-name $KV --name gh-app-install-id --value "<installation-id>"
# The PEM goes in as a multi-line secret; quote carefully:
az keyvault secret set --vault-name $KV --name gh-app-private-key --file ./log-triage-agent.pem
```

## 5. Create the Container Apps environment

```bash
az containerapp env create -g $RG -n $ENV -l $LOC
```

This provisions the shared environment (Log Analytics workspace, dapr if you ever
want it, KEDA scalers).

## 6. Create the Container App with managed identity

```bash
az containerapp create \
  -g $RG -n $APP \
  --environment $ENV \
  --image $ACR.azurecr.io/log-triage-agent:$IMG_TAG \
  --registry-server $ACR.azurecr.io \
  --registry-identity system \
  --ingress external --target-port 8081 \
  --min-replicas 0 --max-replicas 3 \
  --cpu 0.5 --memory 1Gi \
  --system-assigned
```

Two identities are configured here:

- `--registry-identity system` — the system-assigned managed identity pulls images from ACR.
- `--system-assigned` — the same identity will read secrets from Key Vault.

### 6a. Grant the identity access to ACR and Key Vault

```bash
PRINCIPAL=$(az containerapp show -g $RG -n $APP --query identity.principalId -o tsv)
ACR_ID=$(az acr show -g $RG -n $ACR --query id -o tsv)
KV_ID=$(az keyvault show -g $RG -n $KV --query id -o tsv)

# Pull images from ACR
az role assignment create --assignee $PRINCIPAL --role AcrPull --scope $ACR_ID

# Read secrets from Key Vault
az role assignment create --assignee $PRINCIPAL --role "Key Vault Secrets User" --scope $KV_ID
```

### 6b. Wire Key Vault secrets into the app

Container Apps supports Key Vault secret references natively:

```bash
KV_URI=$(az keyvault show -g $RG -n $KV --query properties.vaultUri -o tsv)

az containerapp secret set -g $RG -n $APP --secrets \
    foundry-key=keyvaultref:${KV_URI}secrets/foundry-key,identityref:system \
    webhook-secret=keyvaultref:${KV_URI}secrets/webhook-secret,identityref:system \
    gh-app-id=keyvaultref:${KV_URI}secrets/gh-app-id,identityref:system \
    gh-app-install-id=keyvaultref:${KV_URI}secrets/gh-app-install-id,identityref:system \
    gh-app-private-key=keyvaultref:${KV_URI}secrets/gh-app-private-key,identityref:system
```

The container reads these as regular secrets, but Container Apps refreshes them
from Key Vault automatically — no redeploy needed when you rotate.

### 6c. Set environment variables

```bash
az containerapp update -g $RG -n $APP --set-env-vars \
    LLM_PROVIDER=azure_ai_foundry \
    AZURE_AI_FOUNDRY_ENDPOINT="https://<your-foundry>.services.ai.azure.com/anthropic" \
    AZURE_AI_FOUNDRY_MODEL=claude-sonnet-5 \
    AZURE_AI_FOUNDRY_API_KEY=secretref:foundry-key \
    \
    GITHUB_APP_ID=secretref:gh-app-id \
    GITHUB_APP_INSTALLATION_ID=secretref:gh-app-install-id \
    GITHUB_APP_PRIVATE_KEY_PATH=/secrets/gh-app-private-key.pem \
    GITHUB_WEBHOOK_SECRET=secretref:webhook-secret \
    \
    GITHUB_INPUT_OWNER=<your-org> \
    GITHUB_INPUT_REPO=log-triage \
    GITHUB_OUTPUT_OWNER=<your-org> \
    GITHUB_OUTPUT_REPO=core \
    \
    AUTOFIX_ENABLED=true \
    AUTOFIX_TARGET_OWNER=<your-org> \
    AUTOFIX_TARGET_REPO=<repo-to-fix> \
    AUTOFIX_BASE_BRANCH=main \
    AUTOFIX_TEST_CMD="dotnet build --nologo" \
    AUTOFIX_GIT_USER_NAME="log-triage-agent[bot]" \
    AUTOFIX_GIT_USER_EMAIL="<app-id>+log-triage-agent[bot]@users.noreply.github.com"
```

### 6d. Mount the GitHub App private key as a file

The PEM needs to land on disk (not as an env var) so `Auth.AppAuth` can read it.
Container Apps doesn't natively volume-mount secrets as files, so the simplest
pattern is to write it from an env var on container start:

Add this to the top of `Dockerfile`'s `CMD` chain. Replace the existing `CMD` line:

```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```

Create `entrypoint.sh`:

```bash
#!/bin/sh
set -e
if [ -n "${GITHUB_APP_PRIVATE_KEY:-}" ]; then
  mkdir -p /secrets
  printf '%s' "$GITHUB_APP_PRIVATE_KEY" > /secrets/gh-app-private-key.pem
  chmod 600 /secrets/gh-app-private-key.pem
fi
exec uvicorn api.main:app --host 0.0.0.0 --port 8081
```

Then map the secret as `GITHUB_APP_PRIVATE_KEY` (not as a path) in 6c, instead of
`GITHUB_APP_PRIVATE_KEY_PATH`. Or keep both: write the file from the env var, and
point `_PATH` at the file the entrypoint wrote.

### 6e. Get the public URL and configure the GitHub App webhook

```bash
FQDN=$(az containerapp show -g $RG -n $APP --query properties.configuration.ingress.fqdn -o tsv)
echo "Webhook URL: https://$FQDN/webhooks/github"
```

Go to the GitHub App settings → **Webhook URL** → paste `https://$FQDN/webhooks/github`.
The webhook secret is the value you stored in Key Vault as `webhook-secret`.

## 7. Verify

### 7a. Health check

```bash
curl -i https://$FQDN/healthz
# expect HTTP 200
```

### 7b. Tail logs

```bash
az containerapp logs show -g $RG -n $APP --follow
```

You should see uvicorn startup log lines.

### 7c. End-to-end test

Open an issue in the input repo with one of the labels in `TRIAGE_LABEL`
(`auto-triage-errors` / `auto-triage-exceptions`), or send a manual
webhook from your laptop:

```bash
python scripts/send_test_webhook.py \
    --url https://$FQDN/webhooks/github \
    --payload <issue-body.md> \
    --title "[prod] test" \
    --number 9999 \
    --repo "<your-org>/log-triage" \
    --secret <the same value as KV webhook-secret>
```

Watch the logs for `webhook.accepted` → `locate.picked` → `agent_fix.tool` → `plan.proposed`
→ `fix.pr_opened`. The PR should appear under the bot's identity in
`<your-org>/<repo-to-fix>`.

## 8. Day-2 operations

### Roll a new image

```bash
az acr build -r $ACR -t log-triage-agent:v2 .
az containerapp update -g $RG -n $APP --image $ACR.azurecr.io/log-triage-agent:v2
```

Container Apps rolls one revision at a time; old revisions stay around so you can
roll back instantly:

```bash
az containerapp revision list -g $RG -n $APP -o table
az containerapp revision activate -g $RG -n $APP --revision <prev-revision-name>
```

### Rotate a secret

```bash
az keyvault secret set --vault-name $KV --name webhook-secret --value "$(openssl rand -hex 32)"
# No app restart needed — Container Apps refreshes Key Vault references.
# But: update the GitHub App webhook config to the new value too.
```

### Disable auto-fix during an incident

```bash
az containerapp update -g $RG -n $APP --set-env-vars AUTOFIX_ENABLED=false
```

Triage issues will still be created; PRs won't.

### Scale settings

The default min=0/max=3 is fine for low traffic. Container Apps scales on:

- HTTP concurrency (default rule: 10 concurrent requests per replica)
- Optional KEDA rules — useful if you want CPU/memory based scaling

Webhook traffic is bursty but lightweight; the LLM call is what actually takes time.
Keep min=0 unless cold-start lag (~3–5s) matters for your SLA.

## 9. Locking down ingress

By default the Container App is reachable from anywhere on the public internet.
Two options:

**Allow only GitHub IPs** (cleanest):

```bash
# Get GitHub's hooks IP ranges:
curl -s https://api.github.com/meta | jq -r '.hooks[]'

# Apply as an ingress IP restriction (preview feature in some regions):
az containerapp ingress access-restriction set -g $RG -n $APP \
    --rule-name github-hooks --ip-address <one-cidr> --action Allow
# Repeat for each CIDR. Add a default Deny rule.
```

**Front with Azure Front Door / Application Gateway** if you need WAF, geo-blocking,
or custom TLS certs. Overkill for a single webhook endpoint.

## 10. Cost estimate (low traffic)

| Resource | Monthly |
|---|---|
| Container App (consumption, mostly idle) | ~$2 |
| Container Registry (Basic) | ~$5 |
| Key Vault (operations only) | <$1 |
| Log Analytics (default 30d retention, low volume) | ~$2 |
| Azure AI Foundry (Anthropic Claude) | per-token, depends on usage |
| **Infra subtotal** | **~$10** |

The Foundry bill dominates once issues start flowing through. Plan-mode runs of
the agent loop typically use 5–15k tokens; budget accordingly.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Unauthorized` from GitHub | Installation token expired or App not installed on the repo |
| `403 Resource not accessible by integration` | App permissions don't include the operation (e.g. Issues: Write) — re-grant in App settings |
| Webhook returns `500 GITHUB_WEBHOOK_SECRET not configured` | Env var missing; check `az containerapp show -g $RG -n $APP` |
| Webhook returns `401 Invalid signature` | The secret in `.env` / Key Vault doesn't match the one configured on the GitHub App webhook |
| Agent cold start very slow | First request after scale-to-zero is ~3–5s; set `min-replicas 1` if you want hot |
| `agent_fix.tool` shows repeated edit_file errors | The model is hallucinating context — usually transient; the loop will retry. If persistent, raise `_MAX_ITERATIONS` in `agent_fix.py` |
