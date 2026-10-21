"""Run the build and test commands and record tamper-evident results.

Usage: python run_checks.py <output_dir> [--build CMD] [--test CMD]

Writes build-output.txt, test-output.txt and checks.json into <output_dir>.
Must be run from inside the git worktree to check. Exit code is 0 only when
every step succeeded.
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

TEST_RESULT_RE = re.compile(
    r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored"
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def run_step(name: str, command: str, out_dir: Path) -> dict:
    output_file = f"{name}-output.txt"
    start = time.monotonic()
    with open(out_dir / output_file, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(f"$ {command}\n")
        fh.flush()
        proc = subprocess.run(
            shlex.split(command, posix=os.name != "nt"),
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
    step = {
        "name": name,
        "command": command,
        "exit_code": proc.returncode,
        "duration_s": round(time.monotonic() - start, 1),
        "output_file": output_file,
    }
    if name == "test":
        text = (out_dir / output_file).read_text(encoding="utf-8", errors="replace")
        totals = {"passed": 0, "failed": 0, "ignored": 0}
        for m in TEST_RESULT_RE.finditer(text):
            totals["passed"] += int(m.group(1))
            totals["failed"] += int(m.group(2))
            totals["ignored"] += int(m.group(3))
        step["tests"] = totals
    return step


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir")
    parser.add_argument("--build", default=os.environ.get("FACTORY_BUILD_CMD", "cargo build"))
    parser.add_argument("--test", default=os.environ.get("FACTORY_TEST_CMD", "cargo test"))
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "head": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "started_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "steps": [],
    }
    for name, command in (("build", args.build), ("test", args.test)):
        step = run_step(name, command, out_dir)
        result["steps"].append(step)
        print(f"[{name}] exit={step['exit_code']} ({step['duration_s']}s) {step.get('tests', '')}")
        if step["exit_code"] != 0:
            break

    test_step = next((s for s in result["steps"] if s["name"] == "test"), None)
    result["success"] = (
        len(result["steps"]) == 2
        and all(s["exit_code"] == 0 for s in result["steps"])
        and test_step is not None
        and test_step["tests"]["failed"] == 0
        and test_step["tests"]["passed"] > 0
    )
    (out_dir / "checks.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"success={result['success']} head={result['head'][:12]} dirty={result['dirty']}")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
