"""Paths and runtime settings."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FACTORY_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = FACTORY_DIR / "agents"
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUN_CHECKS_SCRIPT = AGENTS_DIR / "builder" / "tools" / "run_checks.py"


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=FACTORY_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(out).resolve()


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    base_ref: str = "master"
    max_retries: int = 3
    poll_interval: float = 5.0
    claude_cmd: list[str] = field(default_factory=list)
    build_cmd: str = "cargo build"
    test_cmd: str = "cargo test"
    keep_worktrees: bool = False
    repo_root: Path = field(default_factory=_repo_root)
    task_dir: Path = FACTORY_DIR / "task"
    data_dir: Path = FACTORY_DIR / "data"

    @property
    def max_attempts(self) -> int:
        # One initial attempt plus up to `max_retries` retries.
        return 1 + self.max_retries

    @property
    def db_path(self) -> Path:
        return self.data_dir / "factory.db"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def cargo_target_dir(self) -> Path:
        # Shared across task worktrees so each task does not rebuild from scratch.
        return self.data_dir / "cargo-target"

    def resolve_claude_cmd(self) -> list[str]:
        if self.claude_cmd:
            return self.claude_cmd
        env_cmd = os.environ.get("FACTORY_CLAUDE_CMD")
        if env_cmd:
            return shlex.split(env_cmd)
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("claude CLI not found on PATH (set FACTORY_CLAUDE_CMD)")
        return [exe]

    def ensure_dirs(self) -> None:
        for d in (self.task_dir, self.data_dir, self.runs_dir, self.worktrees_dir):
            d.mkdir(parents=True, exist_ok=True)
