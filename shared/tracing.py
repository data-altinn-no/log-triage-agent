"""Langfuse tracing.

Previously Langfuse was a declared dependency with four settings and no call
sites — nothing was ever traced. This wires it to the LangGraph run so the
`autofix.*` outcome rates in docs/AUTO_FIX_DESIGN.md are actually observable.

Fail-soft by design: tracing is observability, not a dependency of the triage
path. A missing package, bad key, or unreachable host degrades to "no traces",
never to a failed triage.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from shared.config import get_settings
from shared.logging import get_logger

log = get_logger(__name__)


@lru_cache
def _handler() -> Any | None:
    """Build the Langfuse callback handler once, or None if unavailable."""
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        log.warning("langfuse.disabled", reason="LANGFUSE_PUBLIC_KEY/SECRET_KEY not set")
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError as exc:
        log.warning("langfuse.unavailable", error=str(exc))
        return None

    try:
        # v3 initialises a client singleton; the handler picks it up by key.
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        handler = CallbackHandler(public_key=settings.langfuse_public_key)
    except Exception as exc:  # noqa: BLE001 — never let tracing break triage
        log.warning("langfuse.init_failed", error=str(exc))
        return None

    log.info("langfuse.enabled", host=settings.langfuse_host)
    return handler


def langchain_config(**metadata: Any) -> dict[str, Any]:
    """Build a LangChain/LangGraph run config, with tracing when enabled.

    Returns a plain config dict; `callbacks` is omitted entirely when Langfuse
    is off, so the graph runs exactly as before.
    """
    config: dict[str, Any] = {}
    if metadata:
        config["metadata"] = metadata
    handler = _handler()
    if handler is not None:
        config["callbacks"] = [handler]
    return config
