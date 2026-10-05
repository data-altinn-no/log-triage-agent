"""Reject patches that put upstream response content into logs or exceptions."""

from __future__ import annotations

import re

_SINK = re.compile(
    r"\.Log(Trace|Debug|Information|Warning|Error|Critical)?\s*\(|new\s+\w*Exception\s*\("
)
_RESPONSE_NAMES = (
    "body|content|json|payload|raw|html|response|responsebody|responsecontent|responsetext"
)
_PLACEHOLDER = re.compile(rf"\{{\s*({_RESPONSE_NAMES})\s*\}}", re.IGNORECASE)
_ARGUMENT = re.compile(rf"[,(]\s*({_RESPONSE_NAMES})\s*[,)]", re.IGNORECASE)


def _added_statements(diff: str) -> list[str]:
    statements, current = [], []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            current = []
            continue
        code = line[1:].strip()
        if current or _SINK.search(code):
            current.append(code)
            if code.endswith(";"):
                statements.append(" ".join(current))
                current = []
    return statements


def find_leaks(diff: str) -> list[str]:
    """Added log or exception statements that carry response content."""
    return [
        s for s in _added_statements(diff)
        if _PLACEHOLDER.search(s) or _ARGUMENT.search(s)
    ]


_PLACEHOLDER_LITERAL = re.compile(r'"(TBD|TODO|FIXME|XXX|CHANGEME)"', re.IGNORECASE)


def find_placeholders(diff: str) -> list[str]:
    return [
        line[1:].strip() for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
        and _PLACEHOLDER_LITERAL.search(line)
    ]


def review(diff: str) -> list[str]:
    """Human-readable reasons to reject the patch; empty when it may proceed."""
    return [f"logs response content: {s}" for s in find_leaks(diff)] + [
        f"placeholder literal: {s}" for s in find_placeholders(diff)
    ]
