"""Loading agent definitions and running them through the Claude Code CLI."""

from __future__ import annotations

import json
import os
import string
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import AGENTS_DIR


@dataclass
class AgentDefinition:
    name: str
    directory: Path
    spec: dict

    @classmethod
    def load(cls, name: str) -> "AgentDefinition":
        directory = AGENTS_DIR / name
        spec = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
        return cls(name=spec.get("name", name), directory=directory, spec=spec)

    def system_prompt(self) -> str:
        """The agent's system prompt followed by each of its reference documents."""
        parts = [(self.directory / self.spec["system_prompt"]).read_text(encoding="utf-8")]
        for ref in self.spec.get("references", []):
            path = (self.directory / ref).resolve()
            parts.append(f"\n\n---\n\n<!-- reference: {path.name} -->\n\n" + path.read_text(encoding="utf-8"))
        return "".join(parts)

    def render_prompt(self, **values: str) -> str:
        template = (self.directory / self.spec["prompt_template"]).read_text(encoding="utf-8")
        return string.Template(template).safe_substitute(values)


@dataclass
class AgentResult:
    exit_code: int
    result_text: str
    is_error: bool
    cost_usd: float | None
    num_turns: int | None
    timed_out: bool = False
    cancelled: bool = False


class AgentRunner:
    def __init__(self, claude_cmd: list[str]):
        self.claude_cmd = claude_cmd
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    def run(
        self,
        agent: AgentDefinition,
        prompt: str,
        cwd: Path,
        out_dir: Path,
        add_dirs: list[Path],
        env: dict[str, str],
    ) -> AgentResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        sys_file = out_dir / f"{agent.name}.system_prompt.md"
        sys_file.write_text(agent.system_prompt(), encoding="utf-8")
        (out_dir / f"{agent.name}.prompt.md").write_text(prompt, encoding="utf-8")
        transcript = out_dir / f"{agent.name}.transcript.jsonl"

        spec = agent.spec
        cmd = [
            *self.claude_cmd,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--append-system-prompt-file", str(sys_file),
            "--permission-mode", spec.get("permission_mode", "default"),
        ]
        if spec.get("model"):
            cmd += ["--model", spec["model"]]
        if spec.get("allowed_tools"):
            cmd += ["--allowedTools", *spec["allowed_tools"]]
        if spec.get("disallowed_tools"):
            cmd += ["--disallowedTools", *spec["disallowed_tools"]]
        if add_dirs:
            cmd += ["--add-dir", *[str(d) for d in add_dirs]]

        child_env = {**os.environ, **env}
        # A parent Claude Code session must not leak into the agent's process.
        for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            child_env.pop(key, None)

        timeout = spec.get("timeout_minutes", 60) * 60
        with self._lock:
            self._cancelled = False
        with open(transcript, "w", encoding="utf-8", errors="replace") as fh:
            with self._lock:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdin=subprocess.PIPE,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    env=child_env,
                    text=True,
                    encoding="utf-8",
                )
            proc = self._proc
            timed_out = False
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
        with self._lock:
            self._proc = None
            cancelled = self._cancelled

        final = _final_result(transcript)
        return AgentResult(
            exit_code=proc.returncode,
            result_text=final.get("result", ""),
            is_error=bool(final.get("is_error")) or proc.returncode != 0,
            cost_usd=final.get("total_cost_usd"),
            num_turns=final.get("num_turns"),
            timed_out=timed_out,
            cancelled=cancelled,
        )


def _final_result(transcript: Path) -> dict:
    result: dict = {}
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("{") and '"type":"result"' in line.replace(" ", ""):
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                pass
    return result


MAX_BLOCK_CHARS = 6000


def _clip(text: str) -> str:
    if len(text) <= MAX_BLOCK_CHARS:
        return text
    return text[:MAX_BLOCK_CHARS] + f"\n… [{len(text) - MAX_BLOCK_CHARS} more characters]"


def parse_transcript(path: Path) -> list[dict]:
    """Turn a stream-json transcript into a flat list of displayable events."""
    events: list[dict] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            events.append({"type": "raw", "text": line})
            continue
        mtype = msg.get("type")
        if mtype == "system" and msg.get("subtype") == "init":
            events.append({
                "type": "system",
                "text": f"session started · model {msg.get('model')} · cwd {msg.get('cwd')}",
            })
        elif mtype == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    events.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    events.append({
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": _clip(json.dumps(block.get("input"), indent=2)),
                    })
        elif mtype == "user":
            content = msg.get("message", {}).get("content", [])
            if isinstance(content, str):
                continue
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                events.append({
                    "type": "tool_result",
                    "id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error")),
                    "text": _clip(str(body or "")),
                })
        elif mtype == "result":
            events.append({
                "type": "result",
                "is_error": bool(msg.get("is_error")),
                "text": msg.get("result", ""),
                "cost_usd": msg.get("total_cost_usd"),
                "num_turns": msg.get("num_turns"),
                "duration_ms": msg.get("duration_ms"),
            })
    return events
