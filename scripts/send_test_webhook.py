"""Send a fake GitHub `issues.opened` webhook to a locally running log-triage-agent.

Usage:
    python scripts/send_test_webhook.py \
        --payload <issue-body.md> \
        --title "[prod] System.NullReferenceException in func-estilda-prod-prod" \
        --number 4221
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_BODY = """### Exception
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


def build_payload(*, number: int, title: str, body: str, repo: str) -> dict:
    return {
        "action": "opened",
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "labels": [{"name": "auto-triage"}, {"name": "prod"}],
        },
        "repository": {"full_name": repo},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8081/webhooks/github")
    parser.add_argument("--secret", default=os.environ.get("GITHUB_WEBHOOK_SECRET", ""))
    parser.add_argument(
        "--payload",
        help="Path to a file containing the issue body (Function template). "
        "If omitted, a baked-in NullReference fixture is used.",
    )
    parser.add_argument(
        "--title",
        default="[prod] System.NullReferenceException in data.altinn.no.api",
    )
    parser.add_argument("--number", type=int, default=9999)
    parser.add_argument("--repo", default="data-altinn-no/log-triage")
    args = parser.parse_args()

    body_text = Path(args.payload).read_text() if args.payload else DEFAULT_BODY
    payload = build_payload(
        number=args.number, title=args.title, body=body_text, repo=args.repo
    )

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "issues"}
    if args.secret:
        sig = "sha256=" + hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = sig

    resp = httpx.post(args.url, content=body, headers=headers, timeout=10.0)
    print(resp.status_code, resp.text)
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
