"""Entry point: `python -m factory_server` (or `uv run factory`) from the factory/ folder."""

from __future__ import annotations

import argparse
import logging
import shlex
from pathlib import Path

from .config import Settings
from .db import Database
from .orchestrator import Orchestrator
from .web import make_server


def main() -> None:
    d = Settings.__dataclass_fields__
    parser = argparse.ArgumentParser(description="Factory server: builder/reviewer agent loop")
    parser.add_argument("--host", default=d["host"].default)
    parser.add_argument("--port", type=int, default=d["port"].default)
    parser.add_argument("--base-ref", default=d["base_ref"].default, help="branch new task branches start from")
    parser.add_argument("--max-retries", type=int, default=d["max_retries"].default)
    parser.add_argument("--poll-interval", type=float, default=d["poll_interval"].default)
    parser.add_argument("--build-cmd", default=d["build_cmd"].default)
    parser.add_argument("--test-cmd", default=d["test_cmd"].default)
    parser.add_argument("--claude-cmd", help="command used to launch Claude Code (default: claude on PATH)")
    parser.add_argument("--task-dir", type=Path, help="folder watched for task files")
    parser.add_argument("--data-dir", type=Path, help="folder for the database, runs and worktrees")
    parser.add_argument("--keep-worktrees", action="store_true", help="keep worktrees of accepted tasks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings(
        host=args.host,
        port=args.port,
        base_ref=args.base_ref,
        max_retries=args.max_retries,
        poll_interval=args.poll_interval,
        build_cmd=args.build_cmd,
        test_cmd=args.test_cmd,
        keep_worktrees=args.keep_worktrees,
        claude_cmd=shlex.split(args.claude_cmd) if args.claude_cmd else [],
    )
    if args.task_dir:
        settings.task_dir = args.task_dir.resolve()
    if args.data_dir:
        settings.data_dir = args.data_dir.resolve()
    settings.ensure_dirs()

    db = Database(settings.db_path)
    orch = Orchestrator(settings, db)
    orch.start()
    server = make_server(settings, db, orch)
    logging.info("watching %s · repo %s · UI at http://%s:%s", settings.task_dir, settings.repo_root,
                 settings.host, settings.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        orch.stop()
        server.server_close()


if __name__ == "__main__":
    main()
