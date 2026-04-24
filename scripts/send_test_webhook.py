"""Send a fake GitHub `issues.opened` webhook to a locally running dan-agent.

Usage:
    python scripts/send_test_webhook.py [--url http://localhost:8081/webhooks/github]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys

import httpx

SAMPLE_BODY = """### Exception
System.NullReferenceException

### Message
Object reference not set to an instance of an object.

### Cloud role
data.altinn.no.api

### Operation
GET /datasets/{id}

### Request path
/datasets/42

### Timestamp
2026-04-24T08:00:00Z

### Correlation id
11111111-2222-3333-4444-555555555555

### Occurrences
17

### Stack trace
```
at Dan.Api.DatasetController.Get(Guid id) in /src/Dan.Api/DatasetController.cs:line 42
at Microsoft.AspNetCore.Mvc.Infrastructure.ActionMethodExecutor.Execute()
```
"""

PAYLOAD = {
    "action": "opened",
    "issue": {
        "number": 9999,
        "title": "[prod] System.NullReferenceException in data.altinn.no.api",
        "body": SAMPLE_BODY,
        "labels": [{"name": "auto-triage"}, {"name": "prod"}],
    },
    "repository": {"full_name": "data-altinn-no/core"},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8081/webhooks/github")
    parser.add_argument("--secret", default=os.environ.get("GITHUB_WEBHOOK_SECRET", ""))
    args = parser.parse_args()

    body = json.dumps(PAYLOAD).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "issues"}
    if args.secret:
        sig = "sha256=" + hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = sig

    resp = httpx.post(args.url, content=body, headers=headers, timeout=10.0)
    print(resp.status_code, resp.text)
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
