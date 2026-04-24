"""GitHub webhook HMAC SHA-256 signature verification."""

import hashlib
import hmac

from fastapi import Header, HTTPException, Request, status

from shared.config import get_settings


async def verify_github_signature(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> bytes:
    secret = get_settings().github_webhook_secret
    body = await request.body()

    if not secret:
        # Fail closed in production; allow only when explicitly empty AND no signature sent (dev).
        if x_hub_signature_256:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GITHUB_WEBHOOK_SECRET not configured",
            )
        return body

    if not x_hub_signature_256 or not x_hub_signature_256.startswith("sha256="):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing signature")

    expected = (
        "sha256="
        + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    return body
