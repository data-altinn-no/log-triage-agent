"""Sandbox workspace for auto-fix: clone a target repo, apply a diff, run tests.

Subprocess-only; never imports git in-process. Each run gets its own temp dir,
cleaned up on context exit even on error.

Intentionally minimal. Heavy sandboxing (containerized test runs, network=none)
is a follow-up; see docs/AUTO_FIX_DESIGN.md.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from shared.logging import get_logger

log = get_logger(__name__)


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


class WorkspaceError(RuntimeError):
    pass


class Workspace:
    """One-shot workspace for a single auto-fix attempt."""

    def __init__(self, root: Path):
        self.root = root
        self.path: Path | None = None

    def __enter__(self) -> Workspace:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=f"wf-{uuid.uuid4().hex[:8]}-", dir=str(self.root)))
        return self

    def __exit__(self, *exc):
        if self.path and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)

    # ---------- git ----------

    def clone(self, clone_url: str, base_branch: str = "main", depth: int = 50) -> None:
        assert self.path is not None
        r = self._run(
            ["git", "clone", "--depth", str(depth), "--branch", base_branch, clone_url, "."],
            cwd=self.path,
            timeout_s=180,
        )
        if not r.ok:
            raise WorkspaceError(f"clone failed: {r.combined[:500]}")

    def head_sha(self) -> str:
        """Current HEAD commit of the checkout."""
        assert self.path is not None
        r = self._run(["git", "rev-parse", "HEAD"], cwd=self.path)
        if not r.ok:
            raise WorkspaceError(f"rev-parse failed: {r.combined[:500]}")
        return r.stdout.strip()

    def checkout_sha(self, sha: str) -> None:
        """Pin the checkout to an exact commit.

        A shallow clone may not contain `sha`, so fetch it explicitly first.
        """
        assert self.path is not None
        if self.head_sha() == sha:
            return
        self._run(["git", "fetch", "--depth", "1", "origin", sha], cwd=self.path, timeout_s=120)
        r = self._run(["git", "checkout", "--force", sha], cwd=self.path)
        if not r.ok:
            raise WorkspaceError(f"checkout {sha[:10]} failed: {r.combined[:500]}")

    def configure_identity(self, name: str, email: str) -> None:
        assert self.path is not None
        self._run(["git", "config", "user.name", name], cwd=self.path, check=True)
        self._run(["git", "config", "user.email", email], cwd=self.path, check=True)

    def apply_diff(self, diff_text: str) -> None:
        """Apply a unified diff. Raises WorkspaceError on failure."""
        assert self.path is not None
        # Try --index first; fall back to 3-way for minor context drift.
        for extra in (["--index"], ["--3way"]):
            r = self._run(
                ["git", "apply", *extra, "-"],
                cwd=self.path,
                input_text=diff_text,
                timeout_s=30,
            )
            if r.ok:
                return
        raise WorkspaceError(f"git apply failed: {r.combined[:500]}")

    def commit(self, message: str) -> str:
        assert self.path is not None
        self._run(["git", "add", "-A"], cwd=self.path, check=True)
        r = self._run(["git", "commit", "-m", message], cwd=self.path)
        if not r.ok:
            raise WorkspaceError(f"commit failed: {r.combined[:500]}")
        sha = self._run(["git", "rev-parse", "HEAD"], cwd=self.path, check=True).stdout.strip()
        return sha

    def create_branch(self, name: str) -> None:
        assert self.path is not None
        r = self._run(["git", "checkout", "-b", name], cwd=self.path)
        if not r.ok:
            raise WorkspaceError(f"checkout -b failed: {r.combined[:500]}")

    def push_branch(self, name: str, authenticated_url: str) -> None:
        assert self.path is not None
        # Use a one-off remote so we don't rewrite origin (which may be a read URL).
        self._run(["git", "remote", "remove", "push-target"], cwd=self.path)  # best-effort
        r = self._run(
            ["git", "remote", "add", "push-target", authenticated_url],
            cwd=self.path,
            check=True,
        )
        r = self._run(
            ["git", "push", "push-target", name],
            cwd=self.path,
            timeout_s=120,
        )
        if not r.ok:
            raise WorkspaceError(f"push failed: {r.combined[:500]}")

    def read_file(self, rel_path: str, max_bytes: int = 200_000) -> str:
        """Read a file from the checkout (for passing context to the LLM)."""
        assert self.path is not None
        fp = self._resolve_safe(rel_path)
        if not fp.exists() or not fp.is_file():
            raise WorkspaceError(f"not a file: {rel_path}")
        data = fp.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    def write_file(self, rel_path: str, content: str) -> None:
        """Overwrite a file inside the workspace. Used by edit-tool agents."""
        assert self.path is not None
        fp = self._resolve_safe(rel_path)
        if not fp.exists():
            raise WorkspaceError(f"not a file: {rel_path}")
        fp.write_text(content, encoding="utf-8")

    def git_diff(self) -> str:
        """Return `git diff` of unstaged changes against HEAD."""
        assert self.path is not None
        r = self._run(["git", "diff", "HEAD"], cwd=self.path)
        if not r.ok:
            raise WorkspaceError(f"git diff failed: {r.combined[:500]}")
        return r.stdout

    def _resolve_safe(self, rel_path: str) -> Path:
        assert self.path is not None
        if Path(rel_path).is_absolute():
            raise WorkspaceError(f"absolute paths not allowed: {rel_path}")
        root = self.path.resolve()
        fp = (root / rel_path).resolve()
        # Path containment, not string prefix: `startswith` would accept a
        # sibling like /tmp/ws-evil for a root of /tmp/ws.
        if not fp.is_relative_to(root):
            raise WorkspaceError(f"path escapes workspace: {rel_path}")
        return fp

    # ---------- commands ----------

    def run_command(self, cmd: str, timeout_s: int) -> RunResult:
        """Run an arbitrary shell-style command inside the workspace."""
        assert self.path is not None
        parts = shlex.split(cmd)
        return self._run(parts, cwd=self.path, timeout_s=timeout_s, scrub_env=True)

    # ---------- internals ----------

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        timeout_s: int = 60,
        check: bool = False,
        scrub_env: bool = False,
    ) -> RunResult:
        env = self._scrubbed_env() if scrub_env else os.environ.copy()
        try:
            proc = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                check=False,
            )
            r = RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")
        except subprocess.TimeoutExpired as exc:

            def _decode(v: bytes | str | None) -> str:
                return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")

            r = RunResult(
                returncode=-1,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                timed_out=True,
            )
        if check and not r.ok:
            raise WorkspaceError(f"{args[0]} failed: {r.combined[:500]}")
        return r

    @staticmethod
    def _scrubbed_env() -> dict[str, str]:
        """Minimal env for test execution. Drops GITHUB_TOKEN, AZURE_*, etc.

        This is not a sandbox — a malicious test can still hit the network or
        read files. It just prevents the agent's own secrets from leaking
        into the child process.
        """
        keep = {"PATH", "HOME", "LANG", "LC_ALL", "PWD", "USER", "SHELL", "TMPDIR"}
        env = {k: v for k, v in os.environ.items() if k in keep}
        # Guarantee a usable PATH even on minimal images.
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        return env
