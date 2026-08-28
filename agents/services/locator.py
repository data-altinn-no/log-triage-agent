"""Locate the likely-responsible source file from a stack trace.

Pure text parsing — no repo I/O, no LLM. Handles the common frame formats:

  Python:  File "foo/bar.py", line 42, in do_thing
  .NET:    at Ns.Class.Method() in /src/.../File.cs:line 42
           at Ns.Class.Method() in C:\\repo\\src\\File.cs:line 42
  Node:    at Object.<anonymous> (/app/foo/bar.js:42:11)
  Java:    at com.foo.Bar.baz(Bar.java:42)

Returns best-effort guesses with confidence. Consumer decides whether to act.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PY_RE = re.compile(r'File "(?P<path>[^"]+?\.py)", line (?P<line>\d+)(?:, in (?P<sym>\S+))?')
_NET_RE = re.compile(r"in (?P<path>\S+?\.(?:cs|fs|vb)):line (?P<line>\d+)", re.IGNORECASE)
_NODE_RE = re.compile(r"\((?P<path>[^\s()]+?\.(?:js|ts|mjs|cjs)):(?P<line>\d+):\d+\)")
_JAVA_RE = re.compile(r"at (?P<sym>[\w$.]+)\((?P<file>[\w$]+\.java):(?P<line>\d+)\)")


@dataclass
class Frame:
    file_path: str
    line: int | None
    symbol: str | None
    raw: str


def extract_frames(stack_trace: str) -> list[Frame]:
    """Return frames in stack order (top first)."""
    if not stack_trace:
        return []
    frames: list[Frame] = []
    for raw_line in stack_trace.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if m := _PY_RE.search(line):
            frames.append(Frame(m.group("path"), int(m.group("line")), m.group("sym"), line))
            continue

        if m := _NET_RE.search(line):
            path = m.group("path").replace("\\", "/")
            path = _trim_repo_path(path)
            sym_match = re.search(r"at\s+([\w.<>`+]+)", line)
            frames.append(
                Frame(path, int(m.group("line")), sym_match.group(1) if sym_match else None, line)
            )
            continue

        if m := _NODE_RE.search(line):
            frames.append(Frame(_trim_repo_path(m.group("path")), int(m.group("line")), None, line))
            continue

        if m := _JAVA_RE.search(line):
            # Basename only; usually unresolvable without a class->file map.
            frames.append(Frame(m.group("file"), int(m.group("line")), m.group("sym"), line))
            continue
    return frames


def _trim_repo_path(path: str) -> str:
    """Heuristically drop absolute-path prefixes so we get a repo-relative-ish path.

    Examples:
      /home/runner/work/repo/repo/src/Foo.cs  -> src/Foo.cs
      C:/build/agents/src/Foo.cs              -> src/Foo.cs
      /app/src/foo.js                         -> src/foo.js
    """
    p = path.replace("\\", "/")

    # Actions checks out at /work/<repo>/<repo>/. Must precede the generic
    # "/work/" marker below, which would leave a path that does not exist.
    m = re.search(r"/work/(?P<repo>[^/]+)/(?P=repo)/", p)
    if m:
        return p[m.end() :]

    markers = ("/src/", "/app/", "/work/")
    for marker in markers:
        idx = p.find(marker)
        if idx != -1:
            return p[idx + 1 :]  # keep "src/..." not "/src/..."
    # Absolute but unrecognised: last 3 segments is the best guess.
    if p.startswith("/") or (len(p) > 2 and p[1] == ":"):
        parts = [s for s in p.split("/") if s and not s.endswith(":")]
        return "/".join(parts[-3:]) if len(parts) >= 3 else p
    return p


def pick_best_frame(frames: list[Frame]) -> Frame | None:
    """Pick the topmost application frame, skipping obvious framework frames."""
    frameworky = (
        "/site-packages/",
        "/node_modules/",
        "/dist/",
        "/System.",
        "Microsoft.",
        "/runtime/",
    )
    for f in frames:
        if not any(marker in f.file_path for marker in frameworky):
            return f
    return frames[0] if frames else None
