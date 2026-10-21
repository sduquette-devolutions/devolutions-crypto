"""SQLite persistence for tasks and their steps."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    status         TEXT NOT NULL,          -- queued | running | accepted | failed | error | cancelled
    branch         TEXT,
    worktree       TEXT,
    base_ref       TEXT,
    base_sha       TEXT,
    max_attempts   INTEGER NOT NULL,
    attempts_used  INTEGER NOT NULL DEFAULT 0,
    summary        TEXT,
    error          TEXT,
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id),
    attempt      INTEGER NOT NULL,         -- 0 for task-level steps (setup/finalize)
    seq          INTEGER NOT NULL,
    kind         TEXT NOT NULL,            -- setup | build | changes | review | finalize
    title        TEXT NOT NULL,
    agent        TEXT,
    status       TEXT NOT NULL,            -- pending | running | succeeded | failed | skipped
    verdict      TEXT,                     -- accept | retry (review steps)
    summary      TEXT,
    exit_code    INTEGER,
    cost_usd     REAL,
    log_path     TEXT,                     -- relative to runs dir
    log_format   TEXT,                     -- text | stream-json
    files        TEXT NOT NULL DEFAULT '[]', -- JSON list of paths relative to runs dir
    meta         TEXT NOT NULL DEFAULT '{}',
    started_at   TEXT,
    finished_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id, seq);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- tasks -------------------------------------------------------------

    def add_task(self, filename: str, title: str, content: str, max_attempts: int) -> int | None:
        ts = now()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO tasks (filename, title, content, status, max_attempts,"
                " created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (filename, title, content, max_attempts, ts, ts),
            )
            return cur.lastrowid if cur.rowcount else None

    def known_filenames(self) -> set[str]:
        with self.connect() as conn:
            return {r["filename"] for r in conn.execute("SELECT filename FROM tasks")}

    def update_task(self, task_id: int, **fields: Any) -> None:
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))

    def get_task(self, task_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id DESC")]

    def next_queued_task(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def recover_interrupted(self) -> None:
        """Mark work left running by a previous server process as interrupted."""
        ts = now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE steps SET status = 'failed', finished_at = ?,"
                " summary = COALESCE(summary, '') || ' [interrupted: server restarted]'"
                " WHERE status IN ('running', 'pending')",
                (ts,),
            )
            conn.execute(
                "UPDATE tasks SET status = 'error', error = 'Interrupted by server restart',"
                " finished_at = ?, updated_at = ? WHERE status = 'running'",
                (ts, ts),
            )

    # -- steps -------------------------------------------------------------

    def add_step(self, task_id: int, attempt: int, kind: str, title: str, agent: str | None = None) -> int:
        with self.connect() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM steps WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO steps (task_id, attempt, seq, kind, title, agent, status, started_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                (task_id, attempt, seq, kind, title, agent, now()),
            )
            return cur.lastrowid

    def update_step(self, step_id: int, **fields: Any) -> None:
        for key in ("files", "meta"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE steps SET {cols} WHERE id = ?", (*fields.values(), step_id))

    def finish_step(self, step_id: int, status: str, **fields: Any) -> None:
        self.update_step(step_id, status=status, finished_at=now(), **fields)

    def get_step(self, step_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
            return _decode_step(row) if row else None

    def list_steps(self, task_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM steps WHERE task_id = ? ORDER BY seq", (task_id,))
            return [_decode_step(r) for r in rows]

    def max_attempt(self, task_id: int) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COALESCE(MAX(attempt), 0) FROM steps WHERE task_id = ?", (task_id,)
            ).fetchone()[0]

    def last_review_feedback(self, task_id: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT meta FROM steps WHERE task_id = ? AND kind = 'review'"
                " AND verdict = 'retry' ORDER BY seq DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return json.loads(row["meta"]).get("feedback") if row else None


def _decode_step(row: sqlite3.Row) -> dict:
    step = dict(row)
    step["files"] = json.loads(step["files"] or "[]")
    step["meta"] = json.loads(step["meta"] or "{}")
    return step
