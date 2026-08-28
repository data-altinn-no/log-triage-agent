"""Stable error fingerprinting for deduplication.

Strategy: normalize the stack trace (strip line numbers, memory addresses, GUIDs,
timestamps) then hash. Two errors with the same normalized signature are treated
as the same issue.
"""

import hashlib
import re

_TRACEPARENT_RE = re.compile(r"\b00-[0-9a-fA-F]{32}-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}\b")
_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONGHEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_QUERY_RE = re.compile(r"\?[^\s\"']*")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?Z?")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUM_RE = re.compile(r"\b\d+\b")
_WS_RE = re.compile(r"\s+")
_LINE_NUM_RE = re.compile(r":line \d+", re.IGNORECASE)
_LOCATION_RE = re.compile(r"in [^\n:]+:\d+", re.IGNORECASE)


def normalize_signature(text: str) -> str:
    s = text or ""
    s = _TRACEPARENT_RE.sub("<traceparent>", s)
    s = _GUID_RE.sub("<guid>", s)
    s = _LONGHEX_RE.sub("<hex>", s)
    s = _HEX_RE.sub("<hex>", s)
    s = _QUERY_RE.sub("?<query>", s)
    s = _TS_RE.sub("<ts>", s)
    s = _LINE_NUM_RE.sub("", s)
    s = _LOCATION_RE.sub("", s)
    s = _NUM_RE.sub("<n>", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def compute_fingerprint(
    exception_type: str | None,
    stack_trace: str | None,
    *,
    message: str | None = None,
    cloud_role: str | None = None,
) -> str:
    """Signature for an error.

    Trace-path issues carry no stack, so without a fallback every one of them
    hashes the same empty string and dedupe treats unrelated failures as
    duplicates of the first. For those, the normalised message plus the role is
    the only signal there is - mirroring Fingerprint.ComputeFromTemplate on the
    monitor side.
    """
    signal = stack_trace or message or ""
    parts = [
        (exception_type or "").strip(),
        (cloud_role or "").strip() if not stack_trace else "",
        normalize_signature(signal),
    ]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
