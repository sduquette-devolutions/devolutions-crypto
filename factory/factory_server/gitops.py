"""Git helpers. Every command's output is appended to a step log."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import IO


class GitError(RuntimeError):
    pass


def git(cwd: Path, *args: str, log: IO[str] | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    out = (proc.stdout + proc.stderr).strip()
    if log is not None:
        log.write(f"$ git {' '.join(args)}\n")
        if out:
            log.write(out + "\n")
        if proc.returncode:
            log.write(f"(exit {proc.returncode})\n")
        log.flush()
    if check and proc.returncode:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {out}")
    return proc.stdout.strip()


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "task"


def branch_exists(repo: Path, branch: str) -> bool:
    return bool(git(repo, "branch", "--list", branch))


def is_worktree(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    try:
        git(path, "rev-parse", "--is-inside-work-tree")
        return True
    except GitError:
        return False


def ensure_worktree(repo: Path, path: Path, branch: str, base_sha: str, log: IO[str]) -> str:
    """Create (or reuse) a worktree at `path` on `branch`. Returns a description."""
    git(repo, "worktree", "prune", log=log)
    if is_worktree(path):
        current = git(path, "rev-parse", "--abbrev-ref", "HEAD", log=log)
        if current != branch:
            raise GitError(f"{path} is a worktree on {current}, expected {branch}")
        return "reused existing worktree"
    if branch_exists(repo, branch):
        git(repo, "worktree", "add", str(path), branch, log=log)
        return "attached existing branch"
    git(repo, "worktree", "add", "-b", branch, str(path), base_sha, log=log)
    return "created branch"


def remove_worktree(repo: Path, path: Path, log: IO[str]) -> None:
    git(repo, "worktree", "remove", "--force", str(path), log=log, check=False)
    git(repo, "worktree", "prune", log=log, check=False)
