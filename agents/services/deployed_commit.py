"""Find the commit that was running when an error was logged."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from shared.logging import get_logger

log = get_logger(__name__)

_ASYNC_STATE_MACHINE = re.compile(r"<(\w+)>d__")
_METHOD_DECLARATION = re.compile(
    r"(public|private|internal|protected)[^=;]*?\b(\w+)\s*(<[^>]*>)?\s*\("
)


@dataclass(frozen=True)
class CommitPick:
    sha: str | None
    verified: bool
    reason: str


def frame_method(symbol: str | None) -> str | None:
    if not symbol:
        return None
    if m := _ASYNC_STATE_MACHINE.search(symbol):
        return m.group(1)
    name = symbol.split("(")[0]
    if name.endswith("..ctor"):
        return name[: -len("..ctor")].split(".")[-1]
    return name.split(".")[-1] or None


def enclosing_method(source: str, line: int) -> str | None:
    lines = source.splitlines()
    if line < 1 or line > len(lines):
        return None
    for text in reversed(lines[:line]):
        if m := _METHOD_DECLARATION.search(text):
            return m.group(2)
    return None


def _parse_timestamp(value: str) -> datetime:
    # App Insights emits 7 fractional digits; fromisoformat accepts at most 6.
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value.strip())
    return datetime.fromisoformat(trimmed.replace("Z", "+00:00"))


def resolve(
    repo, *, branch: str, timestamp: str | None, file_path: str, line: int | None,
    symbol: str | None,
) -> CommitPick:
    """Last commit on `branch` before the error, kept only if the frame lands in its method."""
    want = frame_method(symbol)
    if not timestamp or not line or not want:
        return CommitPick(None, False, "missing timestamp, line or symbol")
    try:
        commits = repo.get_commits(sha=branch, until=_parse_timestamp(timestamp))
        sha = commits[0].sha
        source = repo.get_contents(file_path, ref=sha).decoded_content.decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        log.info("deployed_commit.lookup_failed", error=str(exc))
        return CommitPick(None, False, f"lookup failed: {exc}")

    found = enclosing_method(source, line)
    if found != want:
        where = found or "no method"
        return CommitPick(sha, False, f"line {line} is in {where}, frame says {want}")
    return CommitPick(sha, True, f"line {line} is in {want}")


def method_body(source: str, name: str) -> str | None:
    lines = source.splitlines()
    for start, text in enumerate(lines):
        m = _METHOD_DECLARATION.search(text)
        if not m or m.group(2) != name:
            continue
        depth, opened, body = 0, False, []
        for line in lines[start:]:
            body.append(line)
            depth += line.count("{") - line.count("}")
            opened = opened or "{" in line
            if opened and depth <= 0:
                return "\n".join(body)
    return None


def changes_since(
    repo, *, sha: str, branch: str, file_path: str, symbol: str | None, limit: int = 5,
) -> list[str]:
    """Commits on `branch` touching the file since `sha`, when the failing method has changed."""
    name = frame_method(symbol)
    if not name:
        return []
    try:
        old = repo.get_contents(file_path, ref=sha).decoded_content.decode("utf-8")
        new = repo.get_contents(file_path, ref=branch).decoded_content.decode("utf-8")
        if method_body(old, name) == method_body(new, name):
            return []
        since = repo.get_commit(sha).commit.committer.date
        # Oldest first: the first change after the deployed build is the likeliest fix.
        commits = [
            c for c in repo.get_commits(sha=branch, path=file_path, since=since) if c.sha != sha
        ][::-1]
    except Exception as exc:  # noqa: BLE001
        log.info("deployed_commit.changes_lookup_failed", error=str(exc))
        return []
    summaries = [f"{c.sha[:7]} {c.commit.message.splitlines()[0]}" for c in commits[:limit]]
    if len(commits) > limit:
        summaries.append(f"and {len(commits) - limit} more")
    return summaries or [f"{name} differs on {branch}"]
