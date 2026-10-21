"""Minimal JSON API + static web UI, built on the standard library."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agents import parse_transcript
from .config import STATIC_DIR, Settings
from .db import Database
from .orchestrator import Orchestrator

log = logging.getLogger("factory.web")

MAX_FILE_BYTES = 2 * 1024 * 1024


def make_server(settings: Settings, db: Database, orch: Orchestrator) -> ThreadingHTTPServer:
    runs_dir = settings.runs_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "factory/0.1"

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        # -- helpers ---------------------------------------------------------

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _not_found(self) -> None:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _runs_path(self, rel: str) -> Path | None:
            path = (runs_dir / rel).resolve()
            return path if path.is_relative_to(runs_dir) else None

        # -- routes ----------------------------------------------------------

        def do_GET(self) -> None:
            url = urlparse(self.path)
            p = url.path
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p == "/api/tasks":
                return self._json({"tasks": db.list_tasks()})
            if m := re.fullmatch(r"/api/tasks/(\d+)", p):
                task = db.get_task(int(m[1]))
                if not task:
                    return self._not_found()
                return self._json({"task": task, "steps": db.list_steps(task["id"])})
            if m := re.fullmatch(r"/api/steps/(\d+)/log", p):
                return self._step_log(int(m[1]))
            if m := re.fullmatch(r"/api/steps/(\d+)/file", p):
                return self._step_file(int(m[1]), parse_qs(url.query).get("path", [""])[0])
            if p == "/api/config":
                return self._json({
                    "repo_root": str(settings.repo_root),
                    "task_dir": str(settings.task_dir),
                    "base_ref": settings.base_ref,
                    "max_retries": settings.max_retries,
                    "max_attempts": settings.max_attempts,
                    "build_cmd": settings.build_cmd,
                    "test_cmd": settings.test_cmd,
                })
            return self._not_found()

        def do_POST(self) -> None:
            p = urlparse(self.path).path
            if p == "/api/scan":
                return self._json({"added": orch.scan_tasks()})
            if m := re.fullmatch(r"/api/tasks/(\d+)/(requeue|cancel)", p):
                fn = orch.requeue if m[2] == "requeue" else orch.cancel
                ok = fn(int(m[1]))
                return self._json({"ok": ok}, 200 if ok else HTTPStatus.CONFLICT)
            return self._not_found()

        def _static(self, name: str) -> None:
            path = (STATIC_DIR / name).resolve()
            if not path.is_relative_to(STATIC_DIR.resolve()) or not path.is_file():
                return self._not_found()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        def _step_log(self, step_id: int) -> None:
            step = db.get_step(step_id)
            if not step:
                return self._not_found()
            path = self._runs_path(step["log_path"]) if step["log_path"] else None
            if step["log_format"] == "stream-json" and path:
                return self._json({"format": "events", "events": parse_transcript(path)})
            text = path.read_text(encoding="utf-8", errors="replace") if path and path.exists() else ""
            return self._json({"format": "text", "text": text})

        def _step_file(self, step_id: int, rel: str) -> None:
            step = db.get_step(step_id)
            if not step or rel not in step["files"]:
                return self._not_found()
            path = self._runs_path(rel)
            if not path or not path.is_file():
                return self._not_found()
            data = path.read_bytes()
            truncated = len(data) > MAX_FILE_BYTES
            self._json({
                "path": rel,
                "name": path.name,
                "truncated": truncated,
                "content": data[:MAX_FILE_BYTES].decode("utf-8", errors="replace"),
            })

    return ThreadingHTTPServer((settings.host, settings.port), Handler)
