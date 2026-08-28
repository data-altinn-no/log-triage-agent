"""Tests for the auto-fix sandbox workspace.

This is the component that clones, edits and pushes to other people's repos, so
the containment and env-scrubbing guarantees are worth pinning down.
"""

import subprocess
from pathlib import Path

import pytest

from agents.services.workspace import Workspace, WorkspaceError


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


needs_git = pytest.mark.skipif(not _git_available(), reason="git not installed")


@pytest.fixture
def ws(tmp_path: Path):
    with Workspace(tmp_path / "root") as w:
        yield w


# ---------- path containment ----------

def test_read_and_write_roundtrip(ws: Workspace):
    target = ws.path / "hello.txt"
    target.write_text("original", encoding="utf-8")
    assert ws.read_file("hello.txt") == "original"
    ws.write_file("hello.txt", "replaced")
    assert target.read_text(encoding="utf-8") == "replaced"


def test_traversal_outside_workspace_is_rejected(ws: Workspace):
    with pytest.raises(WorkspaceError, match="escapes workspace"):
        ws.read_file("../../etc/passwd")


def test_absolute_path_is_rejected(ws: Workspace):
    with pytest.raises(WorkspaceError, match="absolute paths not allowed"):
        ws.read_file("/etc/passwd")


def test_sibling_directory_prefix_is_not_treated_as_inside(tmp_path: Path):
    # The old containment check was a string prefix match, so a sibling named
    # like the workspace plus a suffix ("<root>-evil") compared as "inside".
    root = tmp_path / "root"
    with Workspace(root) as w:
        sibling = Path(str(w.path) + "-evil")
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "secret.txt").write_text("leaked", encoding="utf-8")
        with pytest.raises(WorkspaceError):
            w.read_file(f"../{sibling.name}/secret.txt")


def test_write_to_missing_file_is_rejected(ws: Workspace):
    with pytest.raises(WorkspaceError, match="not a file"):
        ws.write_file("does/not/exist.txt", "x")


def test_read_is_byte_capped(ws: Workspace):
    (ws.path / "big.txt").write_text("a" * 5000, encoding="utf-8")
    assert len(ws.read_file("big.txt", max_bytes=100)) == 100


# ---------- lifecycle ----------

def test_workspace_is_removed_on_exit(tmp_path: Path):
    with Workspace(tmp_path / "root") as w:
        path = w.path
        assert path.exists()
    assert not path.exists()


def test_workspace_is_removed_even_when_body_raises(tmp_path: Path):
    path = None
    with pytest.raises(ValueError), Workspace(tmp_path / "root") as w:
        path = w.path
        raise ValueError("boom")
    assert path is not None and not path.exists()


# ---------- command execution ----------

def test_run_command_captures_output_and_status(ws: Workspace):
    ok = ws.run_command("python3 -c 'print(1)'", timeout_s=30)
    assert ok.ok and "1" in ok.stdout

    bad = ws.run_command("python3 -c 'import sys; sys.exit(3)'", timeout_s=30)
    assert not bad.ok and bad.returncode == 3


def test_run_command_times_out_rather_than_hanging(ws: Workspace):
    r = ws.run_command("python3 -c 'import time; time.sleep(10)'", timeout_s=1)
    assert r.timed_out and not r.ok


def test_test_command_env_is_scrubbed_of_secrets(ws: Workspace, monkeypatch):
    # A test run in a target repo must not be able to read the agent's creds.
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecret")
    monkeypatch.setenv("AZURE_AI_FOUNDRY_API_KEY", "azure_supersecret")
    r = ws.run_command(
        "python3 -c 'import os; print(os.environ.get(\"GITHUB_TOKEN\"),"
        " os.environ.get(\"AZURE_AI_FOUNDRY_API_KEY\"))'",
        timeout_s=30,
    )
    assert "supersecret" not in r.combined
    assert "None None" in r.stdout


def test_scrubbed_env_keeps_path(ws: Workspace):
    r = ws.run_command("python3 -c 'import os; print(bool(os.environ[\"PATH\"]))'", timeout_s=30)
    assert "True" in r.stdout


def test_run_command_does_not_use_a_shell(ws: Workspace):
    # shlex.split + no shell: the redirect is passed as a literal argument
    # rather than interpreted, so no file is created.
    ws.run_command("python3 -c 'pass' > pwned.txt", timeout_s=30)
    assert not (ws.path / "pwned.txt").exists()


# ---------- git ----------

@needs_git
def test_head_sha_and_checkout_pin(tmp_path: Path):
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(a, cwd=origin, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (origin / "f.txt").write_text("one", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()
    (origin / "f.txt").write_text("two", encoding="utf-8")
    run("git", "commit", "-qam", "second")

    with Workspace(tmp_path / "root") as w:
        w.clone(str(origin), base_branch="main", depth=50)
        assert w.read_file("f.txt") == "two"
        # Pinning to the plan-time commit is what makes the patch apply.
        w.checkout_sha(first)
        assert w.head_sha() == first
        assert w.read_file("f.txt") == "one"


@needs_git
def test_apply_diff_and_capture(tmp_path: Path):
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(a, cwd=origin, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (origin / "f.txt").write_text("alpha\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")

    with Workspace(tmp_path / "root") as w:
        w.clone(str(origin), base_branch="main", depth=50)
        w.write_file("f.txt", "bravo\n")
        diff = w.git_diff()
        assert "-alpha" in diff and "+bravo" in diff


@needs_git
def test_apply_diff_raises_on_garbage(tmp_path: Path):
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(a, cwd=origin, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (origin / "f.txt").write_text("alpha\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")

    with Workspace(tmp_path / "root") as w:
        w.clone(str(origin), base_branch="main", depth=50)
        with pytest.raises(WorkspaceError, match="git apply failed"):
            w.apply_diff("this is not a diff at all\n")
