"""Stable error fingerprinting for deduplication.

Strategy: normalize the stack trace (strip line numbers, memory addresses, GUIDs,
timestamps) then hash. Two errors with the same normalized signature are treated
as the same issue.
"""

import hashlib
import re

_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUM_RE = re.compile(r"\b\d+\b")
_WS_RE = re.compile(r"\s+")
_LINE_NUM_RE = re.compile(r":line \d+", re.IGNORECASE)
_LOCATION_RE = re.compile(r"in [^\n:]+:\d+", re.IGNORECASE)


def normalize_signature(text: str) -> str:
    s = text or ""
    s = _GUID_RE.sub("<guid>", s)
    s = _HEX_RE.sub("<hex>", s)
    s = _LINE_NUM_RE.sub("", s)
    s = _LOCATION_RE.sub("", s)
    s = _NUM_RE.sub("<n>", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def compute_fingerprint(exception_type: str | None, stack_trace: str | None) -> str:
    parts = [(exception_type or "").strip(), normalize_signature(stack_trace or "")]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
